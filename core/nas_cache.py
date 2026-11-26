"""Per-connection directory batching and a shared, version-checked XML cache.

No NAS writes. Fresh directory metadata validates cached XML and barcode prefixes.
"""
from collections import OrderedDict
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import sqlite3
import time
import zlib
from xml.etree import ElementTree

from config import DATA_DIR, NAS_HOST, NAS_PORT, NAS_ROOT, NAS_USER
from core.cancellation import check_cancelled


class XmlStore:
    def __init__(self, namespace=None, directory=None):
        self.namespace=namespace or json.dumps([NAS_HOST,NAS_PORT,NAS_ROOT,NAS_USER])
        self.path=Path(directory or os.environ.get('DMT_CACHE_DIR',str(DATA_DIR/'cache')))/'nas-xml.sqlite'
        self.db=None;self.disabled=False;self.writes=0
        self.barcode_writes = 0

    def connection(self):
        if self.disabled:return None
        if self.db is None:
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                self.db=sqlite3.connect(self.path,timeout=.2)
                self.db.execute('PRAGMA journal_mode=WAL')
                self.db.execute('CREATE TABLE IF NOT EXISTS xml(key TEXT PRIMARY KEY, version TEXT, body BLOB, touched REAL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS barcodes(key TEXT PRIMARY KEY, version TEXT, value TEXT, touched REAL)')
            except (OSError,sqlite3.Error):
                self.disabled=True;self.close()
        return self.db

    def key(self,path):
        return hashlib.sha256((self.namespace+'\n'+path).encode()).hexdigest()

    def get(self,path,version):
        if version is None:return None
        db=self.connection()
        if db is None:return None
        try:
            row=db.execute('SELECT version,body,touched FROM xml WHERE key=?',(self.key(path),)).fetchone()
            if row and row[0]==version and time.time()-row[2]<86400:
                return zlib.decompress(row[1])
        except (sqlite3.Error,zlib.error):pass
        return None

    def put(self,path,version,body):
        if version is None or len(body)>8*1024*1024:return
        db=self.connection()
        if db is None:return
        try:
            db.execute('INSERT OR REPLACE INTO xml VALUES(?,?,?,?)',(self.key(path),version,zlib.compress(body),time.time()))
            self.writes+=1
            if self.writes%100==1:
                size=db.execute('SELECT COALESCE(SUM(LENGTH(body)),0) FROM xml').fetchone()[0]
                while size>128*1024*1024:
                    db.execute('DELETE FROM xml WHERE key IN (SELECT key FROM xml ORDER BY touched LIMIT 100)')
                    size=db.execute('SELECT COALESCE(SUM(LENGTH(body)),0) FROM xml').fetchone()[0]
            db.commit()
        except sqlite3.Error:
            try:db.rollback()
            except sqlite3.Error:pass

    def get_barcode(self, path, version):
        db = self.connection() if version is not None else None
        if db is not None:
            try:
                row = db.execute('SELECT value FROM barcodes WHERE key=? AND version=? AND touched>?',
                                 (self.key(path), version, time.time() - 86400)).fetchone()
                return row[0] if row else None
            except sqlite3.Error:
                pass
        return None

    def put_barcode(self, path, version, barcode):
        db = self.connection() if version is not None and barcode else None
        if db is None:
            return
        try:
            with db:
                db.execute('INSERT OR REPLACE INTO barcodes VALUES(?,?,?,?)',
                           (self.key(path), version, barcode, time.time()))
                self.barcode_writes += 1
                if self.barcode_writes % 250 == 1:
                    db.execute('DELETE FROM barcodes WHERE touched<=?', (time.time() - 86400,))
        except sqlite3.Error:
            pass

    def close(self):
        if self.db is not None:
            try:self.db.close()
            except sqlite3.Error:pass
            self.db=None


class ScanSftp:
    """One caller/SSH channel per instance; never share a Paramiko client across threads."""
    _dmt_scan_session=True

    def __init__(self,raw,store=None):
        self.raw=raw;self.store=store or XmlStore();self.directories=OrderedDict()
        self.should_stop=None;self.bypass_xml_cache=False;self._dmt_lazy_images=False
        self.started=time.monotonic()
        self.metrics={'directory_reads':0,'directory_hits':0,'stat_reads':0,'xml_reads':0,
                      'xml_cache_hits':0,'xml_bytes':0,'xml_read_seconds':0.,'xml_parse_seconds':0.,'prefetched_files':0}
        self.metrics.update(barcode_reads=0, barcode_bytes=0, barcode_cache_hits=0)

    def __getattr__(self,name):return getattr(self.raw,name)

    def check(self):check_cancelled(self.should_stop)

    def listdir_attr(self,path):
        self.check();path=posixpath.normpath(path)
        if path in self.directories:
            self.metrics['directory_hits']+=1;self.directories.move_to_end(path)
            return list(self.directories[path].values())
        values=self.raw.listdir_attr(path);self.check();self.metrics['directory_reads']+=1
        self.directories[path]={a.filename:a for a in values}
        if len(self.directories)>256:self.directories.popitem(last=False)
        return values

    def listdir(self,path):return [a.filename for a in self.listdir_attr(path)]

    def stat(self,path):
        self.check();self.metrics['stat_reads']+=1;return self.raw.stat(path)

    def metadata(self,path):
        parent,name=posixpath.split(path)
        if parent in self.directories and name in self.directories[parent]:return self.directories[parent][name]
        return self.stat(path)

    @staticmethod
    def version(metadata):
        size=getattr(metadata,'st_size',None);mtime=getattr(metadata,'st_mtime',None)
        # A currently-written or incomplete file must not become a cache hit.
        if not isinstance(size,int) or not isinstance(mtime,(int,float)) or time.time()-mtime<5:return None
        return json.dumps([mtime,size])

    def read_xml(self,path):
        self.check();path=posixpath.normpath(path);before=self.metadata(path);version=self.version(before)
        cached=None if self.bypass_xml_cache else self.store.get(path,version)
        self.check()
        if cached is not None:
            self.metrics['xml_cache_hits']+=1;return cached
        start=time.monotonic();chunks=[]
        with self.raw.open(path,'rb') as file:
            size=getattr(before,'st_size',0)
            if size>=256*1024 and callable(getattr(file,'prefetch',None)):
                file.prefetch(file_size=size,max_concurrent_requests=4);self.metrics['prefetched_files']+=1
            while True:
                self.check();chunk=file.read(65536)
                if not chunk:break
                chunks.append(chunk)
        self.check();body=b''.join(chunks)
        self.metrics['xml_reads']+=1;self.metrics['xml_bytes']+=len(body)
        self.metrics['xml_read_seconds']+=time.monotonic()-start
        # Verify after reading; a growing/replaced file must be read next time.
        after=self.stat(path)
        if version is not None and self.version(after)==version and len(body)==getattr(after,'st_size',-1):
            self.store.put(path,version,body)
        return body

    def read_barcode_prefix(self, path, metadata=None):
        """Identify a Strip with at most 16 KiB from an uncached XML."""
        self.check()
        path = posixpath.normpath(path)
        version = self.version(metadata if metadata is not None else self.metadata(path))
        barcode = self.store.get_barcode(path, version)
        if barcode is not None:
            self.metrics['barcode_cache_hits'] += 1
            return barcode
        body = self.store.get(path, version)
        if body is None:
            with self.raw.open(path, 'rb') as file:
                body = file.read(16 * 1024)
            self.metrics['barcode_reads'] += 1
            self.metrics['barcode_bytes'] += len(body)
        self.check()
        prefix = body[:16 * 1024]
        for stream in (io.BytesIO(prefix), io.StringIO(prefix.decode('cp949', errors='replace'))):
            try:
                for _, element in ElementTree.iterparse(stream, events=('start',)):
                    barcode = element.attrib.get('barcode', '').strip().upper()
                    if barcode and barcode != 'UNKNOWN':
                        self.store.put_barcode(path, version, barcode)
                        return barcode
            except (ElementTree.ParseError, ValueError, LookupError):
                continue
        return None

    def resolve_image(self,path):
        parent,name=posixpath.split(path)
        try:names={a.filename for a in self.listdir_attr(parent)}
        except FileNotFoundError:return path
        if name in names:return path
        stem,extension=posixpath.splitext(name)
        if extension.lower() in ('.bmp','.jpg','.jpeg'):
            for ext in ('.bmp','.jpg','.jpeg'):
                if stem+ext in names:return parent+'/'+stem+ext
        return path

    def report(self):return {**self.metrics,'elapsed_seconds':round(time.monotonic()-self.started,4)}

    def close(self):
        try:self.raw.close()
        finally:self.store.close()
