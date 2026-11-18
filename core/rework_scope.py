"""A shared, explicit re-entry cutoff per model and LOT."""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from config import DATA_DIR


def path_for(model, lot):
    root = Path(os.environ.get('DMT_CACHE_DIR', str(DATA_DIR/'cache')))
    key = hashlib.sha256((model.upper().strip()+'\0'+lot.upper().strip()).encode()).hexdigest()
    return root.parent/'rework-scopes'/(key+'.json')


def load(model, lot):
    path = path_for(model, lot)
    if not path.exists(): return {'enabled':False, 'reuse_xml':False, 'date':''}
    return json.loads(path.read_text(encoding='utf-8'))


def save(model, lot, enabled, date, reuse_xml):
    if not model.strip() or not lot.strip(): raise ValueError('LOT_OR_MODEL_REQUIRED')
    datetime.strptime(date, '%Y-%m-%d')
    value = dict(model=model.upper().strip(), lot=lot.upper().strip(), enabled=bool(enabled),
                 date=date, reuse_xml=bool(reuse_xml))
    path=path_for(model,lot); path.parent.mkdir(parents=True,exist_ok=True)
    import uuid
    tmp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value),encoding='utf-8');tmp.replace(path)
    return value


def cutoff(scope):
    return datetime.strptime(scope['date'],'%Y-%m-%d').timestamp() if scope.get('enabled') else 0.0


def txt_timestamp(path, lot, source_mtime):
    from core.inspection_history import filename_info
    info=filename_info(path,lot)
    if info['inspection_time']:
        return datetime.fromisoformat(info['inspection_time']).timestamp(), 'filename_local_time'
    return source_mtime, 'source_file_mtime'
