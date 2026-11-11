"""Persist completed workflow inputs, isolated by model and LOT.

These are application data, never release assets. Only complete, reviewed
gathering results and original scan payloads may be registered here.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from config import DATA_DIR


PATH_FIELDS = ('vrs_txt_in', 'fvi_txt_in', 'ivs_txt_in', 'ivs_json_in',
               'strip_json_in', 'skipdata_in', 'ski_in', 'txt_in')


def _root():
    return DATA_DIR / 'yield-handoff'


def _read():
    try:
        data = json.loads((_root() / 'latest.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and isinstance(data.get('contexts'), dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def remember(model: str, lot: str, *, payload=None, kind='', output=None, config=None):
    """Register one successful stage. Failure to cache never fails the scan."""
    model, lot = model.strip().upper(), lot.strip().upper()
    if not model or not lot:
        return False
    key = hashlib.sha256((model + '\n' + lot).encode()).hexdigest()
    try:
        data = _read()
        contexts = data.setdefault('contexts', {})
        entry = contexts.setdefault(key, {'model': model, 'lot': lot, 'fields': {}})
        fields = entry['fields']
        updates = {}
        if kind in ('ivs_json', 'strip_json') and payload is not None:
            target = _root() / key / (kind + '-' + uuid4().hex + '.json')
            _write(target, json.dumps(payload, ensure_ascii=False, indent=2))
            updates[kind + '_in'] = str(target.resolve())
            population = payload.get('population', {}) if kind == 'ivs_json' else {}
            if population.get('complete') and population.get('scope') == 'lot':
                ids = population.get('strip_ids', [])
                strip_target = _root() / key / ('strip_json-' + uuid4().hex + '.json')
                _write(strip_target, json.dumps({'schema':'dmt-studio-strip-population-v1','complete':True,'scope':'lot',
                    'model':model, 'lot_id':lot, 'strip_ids':ids, 'strip_count':len(ids),
                    'units_per_strip':population.get('units_per_strip',0)}, ensure_ascii=False))
                updates['strip_json_in'] = str(strip_target.resolve())
                if population.get('total_units'):
                    updates['units_per_strip_in'] = str(population['units_per_strip'])
        if kind in ('VRS', 'FVI', 'IVS') and output is not None:
            # New file per completion: a manually edited input must not silently
            # change underneath Yield when the same stage is rerun.
            target = _root() / key / (kind + '-' + uuid4().hex + '.txt')
            _write(target, output)
            updates[kind.lower() + '_txt_in'] = str(target.resolve())
        if config is not None:
            units = int(config['x_count']) * int(config['y_count'])
            if units > 0:
                updates['units_per_strip_in'] = str(units)
            updates['xout_limit_in'] = str(config['allowance'])
            updates['skipdata_in'] = config.get('skipdata_path', '')
            updates['ski_in'] = config.get('ski_path', '')
        revision = uuid4().hex
        for name, value in updates.items():
            fields[name] = {'value': value, 'revision': revision}
        data['active'] = key
        _write(_root() / 'latest.json', json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except (OSError, ValueError, KeyError, TypeError):
        logging.exception('Could not save Yield workflow handoff')
        return False


def latest():
    data = _read()
    return data.get('contexts', {}).get(data.get('active'), {})


def remember_payload(payload, kind):
    if kind == 'ivs_json' and 'population' in payload and not payload['population'].get('complete'):
        return False
    return remember(str(payload.get('model', '')), str(payload.get('lot_id', '')),
                    payload=payload, kind=kind)
