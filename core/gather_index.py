"""Local TXT body cache shared across LOTs and application restarts."""
import json
import os
import sqlite3
import zlib
from pathlib import Path
from config import DATA_DIR


def read_indexed_lines(path, stat, lot, reader, stats=None):
    def count(key):
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1
    signature = json.dumps([stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size])
    db = DATA_DIR / 'cache' / 'gather-index.sqlite'
    lines = None
    connection = None
    key = os.path.abspath(path)
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db, timeout=10)
        connection.execute('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, signature TEXT, headers TEXT, body BLOB)')
        row = connection.execute('SELECT signature, headers FROM files WHERE path=?', (key,)).fetchone()
        if row and row[0] == signature:
            headers = json.loads(row[1])
            count('disk_hits')
            expected = lot.strip().upper()
            if expected and not any(header == expected or strip.startswith(expected) for header, strip in headers):
                count('other_lot_skipped')
                return []
            payload = connection.execute('SELECT body FROM files WHERE path=?', (key,)).fetchone()[0]
            return json.loads(zlib.decompress(payload))
        count('remote_reads')
        lines = reader(path)
        after = path.stat()
        if (after.st_mtime_ns, after.st_ctime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size):
            raise RuntimeError(f'File changed while reading; retry: {path}')
        headers = []
        for line in lines:
            parts = [part.strip() for part in line.strip().split(',')]
            if len(parts) == 3 and parts[0].isdigit():
                headers.append((parts[1].upper(), parts[2].upper()))
        connection.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?)', (key, signature, json.dumps(headers), zlib.compress(json.dumps(lines, ensure_ascii=False).encode('utf-8'))))
        connection.execute('DELETE FROM files WHERE rowid IN (SELECT rowid FROM files ORDER BY rowid DESC LIMIT -1 OFFSET 10000)')
        connection.commit()
        return lines
    except (sqlite3.Error, ValueError, zlib.error, OSError):
        count('cache_fallbacks')
        if lines is not None:
            return lines
        count('remote_reads')
        return reader(path)
    finally:
        if connection is not None:
            connection.close()


def recent_file(stat, cutoff):
    import os
    created = getattr(stat, 'st_birthtime', stat.st_ctime if os.name == 'nt' else stat.st_mtime)
    return max(created, stat.st_mtime) >= cutoff


def clear_index():
    db = DATA_DIR / 'cache' / 'gather-index.sqlite'
    if db.is_file():
        with sqlite3.connect(db, timeout=10) as connection:
            connection.execute('DELETE FROM files')
