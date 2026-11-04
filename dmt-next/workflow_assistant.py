"""Local, evidence-based LOT history and deterministic input recommendations."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def field_kind(key):
    name = key.rsplit('/', 1)[-1]
    if name in {'units_per_strip_in', 'xout_limit_in', 'x_count_in', 'y_count_in', 'allowance_in', 'fill_code_in', 'exclude_codes_in'}:
        return 'value'
    if name.endswith('_ip_in') or name == 'ip_in':
        return 'value'
    if name in {'txt_in', 'json_in', 'skipdata_in', 'ski_in', 'path_in'} or name.endswith(('_path_in', '_folder_in', '_txt_in', '_json_in')):
        return 'path'
    return None


class WorkflowHistory:
    def __init__(self, root):
        self.root = Path(root) / 'workflow-history'
        self.root.mkdir(parents=True, exist_ok=True)
        self.active = None
        self.error = ''
        self.preferences = {'autofill': False}
        self.secrets = [v for k, v in os.environ.items() if any(s in k.upper() for s in ('PASSWORD', 'TOKEN', 'SECRET')) and len(v) >= 4]
        try:
            self.preferences.update(json.loads((self.root / 'preferences.json').read_text(encoding='utf8')))
        except (OSError, ValueError):
            pass
        # An interrupted run remains available for review, but never trains rules.
        for record in self.records():
            if record.get('outcome') == 'recording':
                record.update(outcome='interrupted', ended=now())
                self.write(record)

    def clean(self, text):
        text = str(text)
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        return re.sub(r'(?i)(password|token|secret)\s*[:=]\s*\S+', r'\1=[REDACTED]', text)

    def atomic(self, path, data):
        temp = path.with_suffix('.tmp')
        def scrub(value):
            if isinstance(value, str): return self.clean(value)
            if isinstance(value, dict): return {k: scrub(v) for k, v in value.items()}
            if isinstance(value, list): return [scrub(v) for v in value]
            return value
        temp.write_text(json.dumps(scrub(data), ensure_ascii=False, indent=2), encoding='utf8')
        os.replace(temp, path)

    def write(self, record):
        self.atomic(self.root / (record['id'] + '.json'), record)

    def records(self):
        result = []
        for path in self.root.glob('run-*.json'):
            try:
                record = json.loads(path.read_text(encoding='utf8'))
                if record.get('schema') == 1:
                    result.append(record)
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda r: r['started'])

    def start(self, model, lot, version):
        if self.active:
            raise ValueError('진행 중인 LOT 기록을 먼저 완료하세요. / 請先結束目前 LOT 紀錄。')
        model, lot = str(model).strip(), str(lot).strip()
        if not model or not lot or len(model) > 100 or len(lot) > 100 or any(c in model + lot for c in '\r\n{}'):
            raise ValueError('모델과 LOT를 입력하세요. / 請輸入機種與 LOT。')
        self.active = dict(schema=1, id='run-' + uuid.uuid4().hex, started=now(), version=version,
                           model=model, lot=lot, outcome='recording', fields={}, events=[], dropped_events=0)
        self.write(self.active)
        return self.active

    def record(self, event, fields=None, **details):
        if not self.active:
            return
        eligible = {k: v for k, v in (fields or {}).items() if field_kind(k) and isinstance(v, str)}
        changes = {k: v for k, v in eligible.items() if self.active['fields'].get(k) != v}
        self.active['fields'].update(eligible)
        self.active['events'].append(dict(time=now(), event=event, changes=changes, **details))
        if len(self.active['events']) > 1000:
            self.active['events'].pop(0)
            self.active['dropped_events'] += 1
        try:
            self.write(self.active)
            self.error = ''
        except OSError as exc:
            self.error = self.clean(exc)

    def finish(self, outcome, note=''):
        if not self.active or outcome not in ('success', 'failed'):
            raise ValueError('No active LOT or invalid outcome')
        if outcome == 'success' and not any(e['event']=='action' for e in self.active['events']):
            raise ValueError('작업을 실행한 뒤 성공을 확인하세요. / 請執行工作後再確認成功。')
        self.active.update(outcome=outcome, ended=now(), note=str(note)[:4000])
        self.write(self.active)
        result = self.active
        self.active = None
        return result

    def recommend(self, model, lot):
        # One vote per distinct LOT, using its latest confirmed successful run.
        runs = {r['lot']: r for r in self.records() if r['outcome'] == 'success' and r['model'] == model and r['lot'] != lot}
        votes = defaultdict(list)
        for old_lot, record in runs.items():
            for key, value in record['fields'].items():
                if not field_kind(key) or not value or '[REDACTED]' in value:
                    continue
                boundary = '(?![A-Za-z0-9])' if old_lot[-1:].isalnum() else ''
                pattern = re.sub(r'(?<![A-Za-z0-9])' + re.escape(old_lot) + boundary, '{LOT}', value) if field_kind(key) == 'path' else value
                votes[key].append((pattern, old_lot))
        recommendations = []
        for key, values in votes.items():
            counts = Counter(pattern for pattern, _ in values)
            pattern, support = counts.most_common(1)[0]
            total = len(values)
            # Ties are ambiguous; never select a candidate arbitrarily.
            if sum(count == support for count in counts.values()) > 1:
                continue
            recommendations.append(dict(key=key, value=pattern.replace('{LOT}', lot), pattern=pattern,
                support=support, total=total, confidence='high' if support >= 3 and support == total else 'review',
                kind=field_kind(key), lots=[old for candidate, old in values if candidate == pattern]))
        return sorted(recommendations, key=lambda r: r['key'])

    def status(self):
        records = self.records()
        by_model = defaultdict(set)
        for record in records:
            if record['outcome']=='success': by_model[record['model']].add(record['lot'])
        return dict(active={k: v for k, v in self.active.items() if k not in ('events', 'fields')} if self.active else None,
                    successfulLots=len({(r['model'], r['lot']) for r in records if r['outcome'] == 'success'}),
                    successfulByModel={model:len(lots) for model,lots in by_model.items()},
                    runs=[{k: r.get(k) for k in ('id','model','lot','started','ended','outcome','note')} for r in records[-30:]][::-1],
                    preferences=self.preferences, error=self.error)

    def export(self):
        return dict(schema=1, exported=now(), description='Local LOT workflow history. Paths and LOT IDs included; no .env or file contents.', runs=self.records())

    def set_preferences(self, autofill):
        self.preferences = {'autofill': bool(autofill)}
        self.atomic(self.root / 'preferences.json', self.preferences)
