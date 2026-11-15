"""A-PC collector transport, immutable input index and collection receipts."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request
import uuid
from contextlib import closing
from config import DATA_DIR
from core.cancellation import OperationCancelled
from core.models import GatherStrip
from core.utils import read_text_lines


def cache_root():return Path(os.environ.get('DMT_CACHE_DIR',str(DATA_DIR/'cache')))
def enabled():return bool(os.environ.get('DMT_COLLECTOR_URL') or os.environ.get('DMT_COLLECTOR_REQUIRED'))


def request(route,payload=None,method=None):
    url=os.environ.get('DMT_COLLECTOR_URL','')
    if not url.startswith('http://127.0.0.1:'):raise RuntimeError('GO_COLLECTOR_UNAVAILABLE')
    body=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(url+route,data=body,method=method or ('POST' if body else 'GET'),
        headers={'Authorization':'Bearer '+os.environ.get('DMT_COLLECTOR_TOKEN',''),'Content-Type':'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:return json.load(response)


def parse_blocks(lines):
    return list(iter_blocks(lines))


def iter_blocks(lines):
    """Preserve each source block, including a later explicit zero-defect block."""
    count=0;active=None;closed=True
    for number,raw in enumerate(lines,1):
        line=raw.strip()
        if not line:continue
        parts=[p.strip() for p in line.split(',')]
        if any(p.upper()=='EOS' for p in parts):
            if active is not None:
                active['end_line']=number;count+=1;yield active;active=None
            closed=True;continue
        if len(parts)==3 and parts[0].isdigit():
            if active is not None:raise ValueError('TXT_BLOCK_NOT_CLOSED')
            active={'lot':parts[1].upper(),'strip':parts[2].upper(),'lines':[],'start_line':number};closed=False
        elif active is not None:
            if len(parts)<5:raise ValueError(f'TXT_INVALID_ROW:{number}')
            try:int(parts[2]);int(parts[3])
            except ValueError:raise ValueError(f'TXT_INVALID_COORDINATE:{number}')
            if parts[1].upper()!=active['strip']:raise ValueError(f'TXT_STRIP_MISMATCH:{number}')
            active['lines'].append(line)
    if not closed or active is not None:raise ValueError('TXT_WRITING_OR_TRUNCATED')
    if not count:raise ValueError('TXT_FORMAT_UNRECOGNIZED')


def indexed_blocks(file,lot):
    root=cache_root();root.mkdir(parents=True,exist_ok=True)
    if Path(file['object']).stat().st_size!=file['size']:raise ValueError('SNAPSHOT_SIZE_MISMATCH')
    with closing(sqlite3.connect(root/'collection-index.sqlite',timeout=15)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS objects(hash TEXT PRIMARY KEY, parser INTEGER, bytes INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS blocks(hash TEXT, ordinal INTEGER, lot TEXT, strip TEXT, payload TEXT, PRIMARY KEY(hash,ordinal))')
        db.execute('CREATE INDEX IF NOT EXISTS blocks_lot ON blocks(lot,strip)')
        db.execute('BEGIN IMMEDIATE')
        found=db.execute('SELECT parser FROM objects WHERE hash=?',(file['sha256'],)).fetchone()
        if found!=(2,):
            path=Path(file['object'])
            with path.open('rb') as source:
                head=source.read(4);source.seek(0);digest=hashlib.file_digest(source,'sha256').hexdigest()
            if digest!=file['sha256']:raise ValueError('SNAPSHOT_HASH_MISMATCH')
            encodings=['utf-16'] if head.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','cp949']
            for index,encoding in enumerate(encodings):
                db.execute('DELETE FROM blocks WHERE hash=?',(file['sha256'],))
                try:
                    with path.open('r',encoding=encoding,errors='strict') as source:
                        for ordinal,block in enumerate(iter_blocks(source)):
                            db.execute('INSERT INTO blocks VALUES(?,?,?,?,?)',(file['sha256'],ordinal,block['lot'],block['strip'],json.dumps(block,ensure_ascii=False)))
                    break
                except UnicodeDecodeError:
                    if index==len(encodings)-1:raise
            db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?)',(file['sha256'],2,path.stat().st_size))
        rows=db.execute('SELECT ordinal,payload FROM blocks WHERE hash=? AND (lot=? OR substr(strip,1,?)=?) ORDER BY ordinal',
            (file['sha256'],lot.upper(),len(lot),lot.upper())).fetchall()
    return [GatherStrip(b['lot'],b['strip'],list(dict.fromkeys(b['lines'])),[file['path'],file['object']],
                source_mtime=file['mtime_ns']/1e9,source_order=order) for order,payload in rows for b in [json.loads(payload)]]


def collect(paths,days,lot,mode,should_stop,progress,force=False):
    run_id=uuid.uuid4().hex
    report={'schema':'dmt-collection-v1','id':run_id,'mode':mode,'lot':lot,'hosts':[],
            'complete':False,'started_at':time.time(),'engine':'go'}
    parsed={};job_id=None;cancelled=False
    try:
        if should_stop():raise OperationCancelled()
        # Retain the administrator's existing remote file-root restrictions.
        if os.environ.get('DMT_REMOTE_SESSION')=='1':
            from remote_paths import allowed_path
            denied=[str(p) for p in paths if not allowed_path(p)]
            if denied:raise PermissionError('A_PC_ROOT_NOT_ALLOWED: '+', '.join(denied))
        job_id=request('/scan',{'roots':[str(p) for p in paths],'days':days,'force':force})['id']
        last_message=''
        while True:
            if should_stop() and not cancelled:
                response=request('/jobs/'+job_id,method='DELETE');cancelled=True
                if response.get('state')=='released':raise OperationCancelled()
            job=request('/jobs/'+job_id+'?summary=1')
            counts=list((job.get('progress') or {}).values())
            message=f"[GO] {len(job['hosts'] or [])}/{len(paths)} · "+f"{sum(p[1] for p in counts)}/{sum(p[0] for p in counts)} TXT · "+'; '.join(job.get('active') or [])
            if message!=last_message:progress(message);last_message=message
            if job['state']!='running':break
            time.sleep(.4)
        try:request('/jobs/'+job_id,method='DELETE')
        except Exception:pass
        if cancelled or job['state']=='cancelled':raise OperationCancelled()
        by_path={h['path']:h for h in (job.get('hosts') or [])}
        for path in paths:
            if should_stop():raise OperationCancelled()
            host=by_path.get(str(path),{'path':str(path),'state':'incomplete','errors':['HOST_REPORT_MISSING'],'files':[],'found':0})
            items=[];failed=[]
            files=sorted(host.get('files') or [],key=lambda f:(f['mtime_ns'],f['path']))
            for position,file in enumerate(files,1):
                if should_stop():raise OperationCancelled()
                try:items.extend((Path(file['path']),item) for item in indexed_blocks(file,lot))
                except Exception as error:failed.append({'path':file['path'],'error':str(error)})
                if position==1 or position%100==0 or position==len(files):progress(f"[INDEX] {path} · {position}/{len(files)} TXT")
            host['parse_errors']=failed;host['processed']=len(host.get('files') or [])-len(failed)
            host['matched_strips']=len({item.strip_id for _,item in items})
            if failed:host['state']='incomplete'
            with closing(sqlite3.connect(cache_root()/'collection-index.sqlite',timeout=15)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS successful_scans(root TEXT, mode TEXT, at TEXT, PRIMARY KEY(root,mode))')
                if host['state']=='complete':db.execute('INSERT OR REPLACE INTO successful_scans VALUES(?,?,?)',(str(path),mode,host.get('completed_at','')))
                last=db.execute('SELECT at FROM successful_scans WHERE root=? AND mode=?',(str(path),mode)).fetchone()
                host['last_successful_at']=last[0] if last else ''
            report['hosts'].append(host);parsed[path]=items
        report['complete']=len(report['hosts'])==len(paths) and all(h['state']=='complete' for h in report['hosts'])
    except OperationCancelled:
        if job_id and not cancelled:
            try:request('/jobs/'+job_id,method='DELETE')
            except Exception:pass
        raise
    except Exception as error:
        if job_id:
            try:request('/jobs/'+job_id,method='DELETE')
            except Exception:pass
        report['hosts']=[{'path':str(p),'state':'incomplete','errors':[str(error)],'files':[],'found':0,'processed':0,'matched_strips':0} for p in paths]
    report['completed_at']=time.time()
    directory=cache_root()/'collection-runs';directory.mkdir(parents=True,exist_ok=True)
    target=directory/(run_id+'.json');encoded=json.dumps(report,ensure_ascii=False).encode('utf-8')
    temporary=target.with_suffix('.tmp');temporary.write_bytes(encoded);temporary.replace(target)
    compact={**report,'hosts':[{k:v for k,v in h.items() if k!='files'} for h in report['hosts']]}
    compact['receipt']={'path':str(target.resolve()),'sha256':hashlib.sha256(encoded).hexdigest()}
    return parsed,compact


def reports_for_inputs(paths,stage_paths=None):
    reports=[];errors=[]
    for path in paths:
        sidecar=path.with_suffix(path.suffix+'.its.json')
        if not sidecar.is_file():continue
        try:
            data=json.loads(sidecar.read_text(encoding='utf-8'));reference=data.get('collection_receipt')
            if not reference:continue
            if hashlib.sha256(path.read_bytes()).hexdigest()!=data.get('sha256'):raise ValueError('COLLECTION_INPUT_CHANGED')
            receipt=Path(reference['path'])
            if not receipt.resolve().is_relative_to((cache_root()/'collection-runs').resolve()):raise PermissionError('COLLECTION_RECEIPT_NOT_ALLOWED')
            if os.environ.get('DMT_REMOTE_SESSION')=='1':
                from remote_paths import allowed_path
                if not receipt.resolve().is_relative_to((cache_root()/'collection-runs').resolve()) and not allowed_path(receipt):raise PermissionError('COLLECTION_RECEIPT_NOT_ALLOWED')
            body=receipt.read_bytes()
            if hashlib.sha256(body).hexdigest()!=reference['sha256']:raise ValueError('COLLECTION_RECEIPT_CHANGED')
            report=json.loads(body)
            if stage_paths:
                stages={stage for stage,items in stage_paths.items() if str(path) in {str(p) for p in items}}
                if stages!={report['mode']}:raise ValueError('COLLECTION_STAGE_MISMATCH')
            if os.environ.get('DMT_REMOTE_SESSION')=='1' and any(not allowed_path(h['path']) for h in report['hosts']):raise PermissionError('COLLECTION_SOURCE_NOT_ALLOWED')
            report['receipt']=reference
            report['hosts']=[{k:v for k,v in h.items() if k!='files'} for h in report['hosts']]
            if report['id'] not in {r['id'] for r in reports}:reports.append(report)
        except Exception as error:errors.append(str(error))
    return {'enabled':bool(reports or errors),'complete':bool(reports) and not errors and all(r['complete'] for r in reports),
            'reports':reports,'errors':errors,'incomplete_hosts':[h for r in reports for h in r['hosts'] if h['state']!='complete']}


def format_report(validation):
    from core.i18n import _
    lines=[_('호기별 수집 검증')+': '+(_('완료') if validation.get('complete') else _('미완료 — 공식 저장 차단'))]
    for report in validation.get('reports',[]):
        for host in report['hosts']:
            label=_('정상 수집') if host['state']=='complete' and host.get('matched_strips') else _('검색 완료 · LOT 기록 없음') if host['state']=='complete' else _('수집 미완료')
            lines.append(f"{report['mode']} · {host['path']} · {label} · "+_('발견/처리/실패')+f": {host.get('found',0)}/{host.get('processed',0)}/{len(host.get('errors') or [])+len(host.get('parse_errors') or [])}")
            lines.extend((host.get('errors') or [])[:3])
            lines.extend(f"{e['path']}: {e['error']}" for e in (host.get('parse_errors') or [])[:3])
    lines.extend(validation.get('errors',[]))
    lines.append(_('LOT 기록 없음은 검사 완료 또는 불량 0개를 뜻하지 않습니다.'))
    return '\n'.join(lines)


def snapshot_allowed(path,trace):
    """Archive reads require a bound receipt AND an approved original source."""
    try:
        path=Path(path).resolve();root=(cache_root()/'collector'/'objects').resolve()
        if path.parent!=root or len(path.stem)!=64:return False
        from remote_paths import allowed_path
        for report in trace.get('collection_validation',{}).get('reports',[]):
            reference=report.get('receipt',{});receipt=Path(reference['path']).resolve()
            if not receipt.is_relative_to((cache_root()/'collection-runs').resolve()):continue
            body=receipt.read_bytes()
            if hashlib.sha256(body).hexdigest()!=reference.get('sha256'):continue
            data=json.loads(body)
            for host in data['hosts']:
                if not allowed_path(host['path']):continue
                for file in host.get('files') or []:
                    if Path(file['object']).resolve()==path and file['sha256']==path.stem:
                        return hashlib.sha256(path.read_bytes()).hexdigest()==file['sha256']
    except (OSError,ValueError,KeyError,TypeError):pass
    return False


def write_export(path,text,history,receipt=None):
    from core.yield_handoff import _write
    path=Path(path)
    _write(path,text)
    _write(path.with_suffix(path.suffix+'.its.json'),json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'strips':history,'collection_receipt':receipt},ensure_ascii=False))
