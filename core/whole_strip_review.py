"""Keep suspect whole-strip TXT evidence separate from official yield."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from config import DATA_DIR
from core.report_trace import file_version


def archive_root():
    cache = Path(os.environ.get('DMT_CACHE_DIR', DATA_DIR / 'cache'))
    return cache.parent / 'whole-strip-review'


def _snapshot(source, target, version):
    if file_version(source) != version:
        raise ValueError('TXT_CHANGED_RECALCULATE')
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with source.open('rb') as original:
                shutil.copyfileobj(original, stream)
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if file_version(source) != version:
            raise ValueError('TXT_CHANGED_RECALCULATE')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class WholeStripReview:
    def __init__(self, ski_path, ski_version):
        self.root = archive_root()
        self.database = self.root / 'review.sqlite'
        self.ski_path = ski_path
        self.ski_version = ski_version
        self.ski = self._identity(ski_path, ski_version) if ski_path and ski_version else None
        self.rows = []

    @staticmethod
    def _identity(path, version):
        return json.dumps([os.path.normcase(str(Path(path).resolve())), version])

    def check(self, strip, process, source, source_version, ski_count, units):
        partial_ski = 0 < ski_count < units
        if not partial_ski and not self.database.is_file():
            return None
        source = Path(source)
        if source_version is None:
            raise ValueError('TXT_CHANGED_RECALCULATE')
        if partial_ski and file_version(self.ski_path) != self.ski_version:
            raise ValueError('SKI_CHANGED_RECALCULATE')
        source_id = self._identity(source, source_version)
        key = (source_id, strip, process)
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=30)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS reviews (
                source_id TEXT, strip TEXT, process TEXT, first_ski TEXT NOT NULL, backup TEXT,
                PRIMARY KEY (source_id, strip, process))''')
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT first_ski,backup FROM reviews WHERE source_id=? AND strip=? AND process=?', key).fetchone()
            backup = saved[1] if saved else None
            if not backup:
                if not partial_ski and (not saved or ski_count >= units):
                    if saved:
                        db.execute('DELETE FROM reviews WHERE source_id=? AND strip=? AND process=?', key)
                    return None
                if not saved:
                    if self.ski is None:
                        raise ValueError('SKI_CHANGED_RECALCULATE')
                    db.execute('INSERT INTO reviews VALUES (?,?,?,?,NULL)', (*key, self.ski))
                elif partial_ski and self.ski and self.ski != saved[0]:
                    backup = hashlib.sha256(source_id.encode()).hexdigest() + '/' + source.name
                    _snapshot(source, self.root / backup, source_version)
                    db.execute('UPDATE reviews SET backup=? WHERE source_id=? AND strip=? AND process=?', (backup, *key))
            if backup:
                _snapshot(source, self.root / backup, source_version)
        row = {
            'strip_id': strip, 'process': process, 'source': str(source),
            'ski_count': ski_count, 'txt_count': units,
            'status': 'archived' if backup else 'awaiting_ski',
            'backup': backup,
        }
        self.rows.append(row)
        return row['status']


def export_evidence(rows, target):
    archived = [row for row in rows if row['status'] == 'archived']
    if not archived:
        raise ValueError('NO_ARCHIVED_WHOLE_STRIP_TXT')
    root = archive_root()
    with ZipFile(target, 'w', compression=ZIP_DEFLATED) as package:
        for name in sorted({row['backup'] for row in archived}):
            package.write(root / name, 'TXT/' + name)
        package.writestr('review.json', json.dumps(archived, ensure_ascii=False, indent=2))
