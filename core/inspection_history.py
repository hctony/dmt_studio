"""Durable, source-bound operator history, shared by A-PC sessions.

The archive is separate from the disposable parser cache. Original bytes are
compressed once per SHA256; repeated collections only update last_seen.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import time
import zlib

from config import DATA_DIR

TIME_LABELS={'filename_local_time':'파일명의 검사 시각 (A PC 현지 시각)',
             'file_mtime_only':'파일명에 날짜 없음 — 수정 시각만 확인 가능',
             'invalid_filename_time':'파일명 날짜·시각 오류',
             'unverified_filename':'파일명 형식 또는 LOT 불일치 — 작업자 확인 불가'}


def archive_path():
    # DMT_CACHE_DIR is shared between the server and its private client sessions.
    root = Path(os.environ.get('DMT_CACHE_DIR', str(DATA_DIR / 'cache')))
    return root.parent / 'inspection-history' / 'history.sqlite'


def connect():
    path = archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        for attempt in range(20):
            try:
                db.execute('PRAGMA journal_mode=WAL')
                break
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt == 19:
                    raise
                time.sleep(.05)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS originals(hash TEXT PRIMARY KEY, content BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS inspections(
            id TEXT PRIMARY KEY, mode TEXT, model TEXT, lot TEXT, strip TEXT,
            operator TEXT, inspection_time TEXT, time_basis TEXT,
            source_path TEXT, source_mtime_ns INTEGER, hash TEXT,
            ordinal INTEGER, start_line INTEGER, end_line INTEGER, defects INTEGER,
            first_seen REAL, last_seen REAL);
        CREATE INDEX IF NOT EXISTS inspections_strip ON inspections(strip,mode);
        CREATE INDEX IF NOT EXISTS inspections_lot ON inspections(lot,operator);
        ''')
    except Exception:
        db.close()
        raise
    return db


def filename_info(path, lot):
    name = PureWindowsPath(str(path)).stem
    parts = name.split('_', 3)
    result = {'model': '', 'operator': '', 'inspection_time': '', 'time_basis': 'unverified_filename'}
    if len(parts) != 4 or parts[1].upper() != lot.upper():
        return result
    result.update(model=parts[0], operator=parts[2], time_basis='file_mtime_only')
    suffix = parts[3]
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{6}', suffix):
        try:
            result.update(inspection_time=datetime.strptime(suffix, '%Y-%m-%d %H%M%S').isoformat(' '),
                          time_basis='filename_local_time')
        except ValueError:
            result['time_basis'] = 'invalid_filename_time'
    return result


def decode_original(body):
    encodings = ['utf-16'] if body.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'cp949']
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('HISTORY_ENCODING_INVALID')


def record_file(file, lot, mode):
    """Archive all blocks for this collected LOT before merging/rework selection."""
    from core.collection import iter_blocks
    if mode not in {'IVS', 'VRS', 'FVI'}:
        raise ValueError('HISTORY_STAGE_INVALID')
    with closing(connect()) as db, db:
        existing = db.execute('SELECT COUNT(*) FROM inspections WHERE mode=? AND source_path=? AND hash=? AND source_mtime_ns=? AND lot=?',
                              (mode, file['path'], file['sha256'], file['mtime_ns'], lot.upper())).fetchone()[0]
        if existing:
            db.execute('UPDATE inspections SET last_seen=? WHERE mode=? AND source_path=? AND hash=? AND source_mtime_ns=? AND lot=?',
                       (time.time(), mode, file['path'], file['sha256'], file['mtime_ns'], lot.upper()))
            return existing
    body = Path(file['object']).read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if digest != file['sha256'] or len(body) != file['size']:
        raise ValueError('HISTORY_SNAPSHOT_CHANGED')
    blocks = [(ordinal, block) for ordinal, block in enumerate(iter_blocks(decode_original(body).splitlines()))
              if block['lot'] == lot.upper() or block['strip'].startswith(lot.upper())]
    if not blocks:
        return 0
    info = filename_info(file['path'], lot)
    now = time.time()
    with closing(connect()) as db, db:
        db.execute('INSERT OR IGNORE INTO originals VALUES(?,?)', (digest, zlib.compress(body)))
        for ordinal, block in blocks:
            identity = json.dumps([mode, file['path'], digest, file['mtime_ns'], ordinal], ensure_ascii=False)
            key = hashlib.sha256(identity.encode()).hexdigest()
            db.execute('''INSERT INTO inspections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen''',
                (key, mode, info['model'], block['lot'], block['strip'], info['operator'],
                 info['inspection_time'], info['time_basis'], file['path'], file['mtime_ns'], digest,
                 ordinal, block['start_line'], block['end_line'], len(block['lines']), now, now))
    return len(blocks)


def permitted(path):
    if os.environ.get('DMT_REMOTE_SESSION') != '1':
        return True
    from remote_paths import allowed_path
    return allowed_path(path)


def search(lot='', strip='', operator='', mode='', limit=500):
    if not lot.strip() and not strip.strip() and not operator.strip():
        raise ValueError('LOT_OR_STRIP_REQUIRED')
    clauses = []; values = []
    for field, value in [('lot', lot.upper().strip()), ('strip', strip.upper().strip()),
                         ('operator', operator.strip()), ('mode', mode.strip())]:
        if value:
            clauses.append(field + '=?'); values.append(value)
    results = []
    with closing(connect()) as db:
        # Filter permissions before limiting: client sessions only see shared roots.
        cursor = db.execute('SELECT * FROM inspections WHERE ' + ' AND '.join(clauses) +
                            ' ORDER BY source_mtime_ns DESC,id', values)
        for row in cursor:
            if permitted(row['source_path']):
                results.append(dict(row))
                if len(results) >= min(max(int(limit), 1), 501):
                    break
    return results


def original(event_id):
    with closing(connect()) as db:
        row = db.execute('SELECT * FROM inspections WHERE id=?', (event_id,)).fetchone()
        if row is None or not permitted(row['source_path']):
            raise PermissionError('HISTORY_SOURCE_NOT_ALLOWED')
        data = db.execute('SELECT content FROM originals WHERE hash=?', (row['hash'],)).fetchone()
        if data is None:
            raise ValueError('HISTORY_ORIGINAL_MISSING')
        body = zlib.decompress(data[0])
        if hashlib.sha256(body).hexdigest() != row['hash']:
            raise ValueError('HISTORY_ORIGINAL_CHANGED')
        text = decode_original(body)
        block = '\n'.join(text.splitlines()[row['start_line'] - 1:row['end_line']])
        return dict(row), body, block
