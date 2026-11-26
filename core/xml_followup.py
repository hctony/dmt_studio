"""A PC's shared image lookup queue and historical verification results."""
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import time
import uuid

from core.report_codes import excel_code
from core.utils import unit_idx_to_xy


IMAGE_JOB = 'image-lookup-v1'


def connect():
    from core.collection import cache_root
    root = cache_root().parent / 'xml-followup'
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'history.sqlite', timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, model TEXT, lot TEXT, created REAL, updated REAL,
        status TEXT, baseline TEXT, details TEXT, xml TEXT
    )''')
    return db


def enqueue(model, summary, high_codes):
    lot = str(summary['lot_id']).strip()
    high = {entry.split()[0] for entry in high_codes if entry}
    if not high:
        return None
    if not model or not lot or not summary['xml_followup_inputs']:
        raise ValueError('XML_FOLLOWUP_MISSING_MODEL_LOT_OR_TXT')
    trace = summary['report_trace']
    from core.yield_handoff import context
    grid = context(model, lot).get('gathering_grid')
    evidence = summary['xml_followup_evidence']
    if grid and grid[0] * grid[1] == summary['units_per_strip']:
        evidence = {strip: {**row, 'width': grid[0]} for strip, row in evidence.items()}
    assignments = {}
    for group in trace['groups']:
        code = excel_code(group['code'])
        if code in high:
            assignments.setdefault(group['strip_id'], []).extend(
                [x, y, code] for x, y in group['coords'])
    scope = dict(summary.get('rework_scope') or {})
    if scope.get('reuse_xml'):
        # A TXT-only recheck keeps the original inspection XML eligible.
        scope = {}
    baseline = {'units': summary['units_per_strip'], 'strips': trace['selected_strip_ids'],
                'evidence': evidence, 'scope': scope, 'high': sorted(high), 'image_assignments': assignments,
                'total_units': summary['total_units']}
    baseline = image_baseline(baseline)
    if not baseline['image_assignments']:
        return None
    text = json.dumps(baseline, sort_keys=True)
    now = time.time()
    identity = uuid.uuid4().hex
    with closing(connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT id FROM jobs WHERE model=? AND lot=? AND baseline=? ORDER BY created DESC LIMIT 1',
                              (model, lot, text)).fetchone()
        if existing:
            return existing['id']
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                   (identity, model, lot, now, now, 'queued', text, '{}', ''))
    return identity


def image_baseline(baseline):
    high = set(baseline.get('high', []))
    evidence = baseline.get('evidence', {})
    assignments = {}
    for strip, rows in baseline.get('image_assignments', {}).items():
        matches = sorted({(x, y, code) for x, y, code in rows if code in high})
        if matches and strip in evidence and evidence[strip].get('width'):
            assignments[strip] = matches
    return {
        'kind': IMAGE_JOB,
        'strip_count': baseline.get('strip_count', len(baseline.get('strips', []))),
        'total_units': baseline.get('total_units', 0),
        'scope': baseline.get('scope', {}),
        'high': sorted(high),
        'image_assignments': assignments,
        'evidence': {strip: {'width': evidence[strip]['width']} for strip in assignments},
    }


def recover():
    with closing(connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT id,status,baseline FROM jobs WHERE status IN ('queued','running','cancelling')").fetchall():
            baseline = json.loads(row['baseline'])
            if baseline.get('kind') != IMAGE_JOB:
                baseline = image_baseline(baseline)
                status = 'pending_images' if baseline['image_assignments'] else 'images_skipped'
                db.execute('UPDATE jobs SET status=?, updated=?, baseline=? WHERE id=?',
                           (status, time.time(), json.dumps(baseline, sort_keys=True), row['id']))
            elif row['status'] != 'queued':
                db.execute("UPDATE jobs SET status='pending_images', updated=? WHERE id=?",
                           (time.time(), row['id']))


def request_images(identity):
    with closing(connect()) as db, db:
        db.execute("""UPDATE jobs SET status='queued', updated=?, details='{}' WHERE id=?
                      AND status IN ('pending_images','images_missing','images_failed')""", (time.time(), identity))


def cancel_images(identity):
    with closing(connect()) as db, db:
        db.execute("""UPDATE jobs SET status=CASE WHEN status='running' THEN 'cancelling' ELSE 'pending_images' END,
                      updated=? WHERE id=? AND status IN ('queued','running')""", (time.time(), identity))


def is_cancelling(identity):
    with closing(connect()) as db:
        row = db.execute('SELECT status FROM jobs WHERE id=?', (identity,)).fetchone()
        return bool(row and row['status'] == 'cancelling')


def claim():
    with closing(connect()) as db, db:
        row = db.execute('''UPDATE jobs SET status='running', updated=? WHERE id=(
            SELECT id FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1)
            AND NOT EXISTS(SELECT 1 FROM jobs WHERE status IN ('running','cancelling'))
            RETURNING id, model, lot, baseline''', (time.time(),)).fetchone()
        return dict(row) if row else None


def finish(identity, status, details, xml=None):
    with closing(connect()) as db, db:
        db.execute("""UPDATE jobs SET status=CASE WHEN status='cancelling' THEN 'pending_images' ELSE ? END,
                      updated=?, details=CASE WHEN status='cancelling' THEN '{}' ELSE ? END,
                      xml=CASE WHEN status='cancelling' THEN '' ELSE ? END WHERE id=?""",
                   (status, time.time(), json.dumps(details, ensure_ascii=False),
                    json.dumps(xml, ensure_ascii=False) if xml else '', identity))


def list_jobs(model='', lot='', limit=200):
    with closing(connect()) as db:
        return [dict(row) for row in db.execute('''
            SELECT id,model,lot,status,created,updated FROM jobs
            WHERE instr(upper(model), upper(?)) > 0 AND instr(upper(lot), upper(?)) > 0
            ORDER BY created DESC,id DESC LIMIT ?''', (model, lot, limit))]


def read_job(identity, *, include_xml=False):
    with closing(connect()) as db:
        columns = '*' if include_xml else "id,model,lot,created,updated,status,baseline,details,xml<>'' AS xml"
        row = db.execute(f'SELECT {columns} FROM jobs WHERE id=?', (identity,)).fetchone()
    return dict(row) if row else None


def compare(baseline, payload):
    from core.ivs import load_records_from_payload
    txt = set(baseline['strips'])
    pop = payload.get('population', {})
    xml = set(pop.get('strip_ids', []))
    selected = payload.get('latest_selection', {}).get('selected', {})
    older = []
    for stage, blocks in baseline['processes'].items():
        for strip, item in blocks.items():
            epoch = selected.get(strip, {}).get('epoch_start')
            if epoch and item['time'] < epoch:
                older.append(f'{stage} {strip}')
    from core.image_catalog import ImageCatalog
    records = load_records_from_payload(payload)
    image_counts = dict.fromkeys(baseline['high'], 0)
    image_counts.update(ImageCatalog(image_records(baseline, records)).counts())
    missing_coords = []
    expected = {strip: {tuple(coord) for coord in evidence['coords']}
                for strip, evidence in baseline['evidence'].items()}
    for record in records:
        row = baseline['evidence'].get(record.strip_id)
        if row and record.is_rate_defect and record.strip_id in txt:
            coord = unit_idx_to_xy(record.unit_idx, row['width'])
            if coord and coord not in expected[record.strip_id]:
                missing_coords.append(f'{record.strip_id} X:{coord[0]} Y:{coord[1]} {record.ivs_1}')
    issues = {'xml_only': sorted(xml - txt), 'txt_only': sorted(txt - xml),
              'older_txt': sorted(set(older)), 'xml_only_coords': sorted(set(missing_coords))}
    complete = bool(pop.get('complete') and payload.get('strip_history', {}).get('complete'))
    status = 'incomplete' if not complete else 'difference' if any(issues.values()) else 'verified'
    return status, {**issues, 'image_counts': image_counts, 'xml_strip_count': len(xml)}


def image_records(baseline, records):
    """Relate original XML images to the final report's assigned defect."""
    assignments = {
        strip: {(x, y): code for x, y, code in rows}
        for strip, rows in baseline.get('image_assignments', {}).items()
    }
    selected = []
    for record in records:
        assigned = assignments.get(record.strip_id)
        if not record.images or not assigned:
            continue
        if record.is_dummy:
            code = excel_code(record.ivs_1)
            if code not in assigned.values():
                continue
        else:
            coord = unit_idx_to_xy(record.unit_idx, baseline['evidence'][record.strip_id]['width'])
            code = assigned.get(coord)
            if not code:
                continue
        selected.append(replace(record, ivs_1=code))
    return selected
