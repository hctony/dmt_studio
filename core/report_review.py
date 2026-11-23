"""Report gates, OT14 exclusion and source-bound manual code review."""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import os
from random import Random
from datetime import datetime, timezone
from config import DATA_DIR
from core.report_trace import SKI_UNKNOWN_ALLOCATED

UNRESOLVED={'UNKNOWN'}
REVIEWABLE=UNRESOLVED|{'9999'}
MIN_ALLOCATION_CODE_COUNT = 40

def mismatch_rows(summary):
    key='final_xout' if summary.get('mode')=='process' else 'xout_count'
    return [row for row in summary.get('per_strip_rows',[])
            if row.get('whole_strip_awaiting_ski') or row.get('its_txt_extra') or int(row.get(key,0))!=int(row.get('ski_xout_count',0))]

def review_binding(summary):
    trace=summary.get('_original_report_trace',summary.get('report_trace',{}))
    body={'lot':summary.get('lot_id'),'units':summary.get('units_per_strip'),'groups':trace.get('groups',[]),'versions':trace.get('file_versions',{})}
    return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def changed_paths(trace):
    from core.report_trace import file_version
    from core.ot14_audit import manifest
    changed = [path for path, version in trace.get('file_versions', {}).items() if file_version(path) != version]
    if 'rpt_audit_versions' in trace:
        previous = trace['rpt_audit_versions']
        current = manifest(trace.get('bbt_folder', ''))
        # New RPTs may contain headers for a selected Strip regardless of their filename.
        changed.extend(current.keys() - previous.keys())
    return sorted(set(changed))

def sources_changed(summary):
    trace=summary.get('_original_report_trace',summary.get('report_trace',{}))
    return changed_paths(trace)

def record_its_alert(summary, model):
    """Notify once for a source snapshot, shared by desktop and A-PC sessions."""
    if not summary.get('its_gathering_differences'):
        return False
    from core.collection import cache_root
    trace = summary.get('_original_report_trace', summary.get('report_trace', {}))
    sources = {os.path.normcase(os.path.abspath(path)): version for path, version in trace.get('file_versions', {}).items()}
    body = [model.strip().upper(), summary['lot_id'], sources]
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    directory = cache_root().parent / 'its-gathering-alerts'
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with (directory / key).open('x', encoding='utf-8'):
            pass
    except FileExistsError:
        return False
    return True

def _path(summary):return DATA_DIR/'report-reviews'/(review_binding(summary)+'.json')


def _allocate_ski_unknown(summary, trace):
    strips = trace.get('strips', {})
    pending = set()
    confirmed = Counter()
    for group in trace.get('groups', []):
        code, strip = group['code'].upper(), group['strip_id']
        coords = group['coords']
        if code == 'UNKNOWN':
            ski = {tuple(coord) for coord in strips.get(strip, {}).get('ski_coords', [])}
            pending.update((strip, x, y) for x, y in coords if (x, y) in ski)
        elif group['reason'] in {'gather', 'ivs_recovery'} and code not in {'OT06', 'OT14'} and re.fullmatch(r'[A-Z]{2}[0-9]{2}', code):
            confirmed[code] += len(coords)
    ranked = sorted((code for code, count in confirmed.items() if count >= MIN_ALLOCATION_CODE_COUNT),
                    key=lambda code: (confirmed[code], code))
    if not pending or not ranked:
        return {}
    trim = len(ranked) // 4
    middle = ranked[trim:len(ranked) - trim]
    coordinates = sorted(pending)
    seed = [summary.get('lot_id'), [(code, confirmed[code]) for code in middle], coordinates]
    random = Random(json.dumps(seed, ensure_ascii=False))
    allocations = {}
    for index, coord in enumerate(coordinates):
        slot = index % len(middle)
        if slot == 0:
            random.shuffle(middle)
        allocations[coord] = middle[slot]
    return allocations


def prepare_summary(summary):
    """Keep raw coordinate verification intact; apply policy only to yield metrics."""
    if not summary.get('ot14_policy'):
        summary['gross_total_units']=int(summary.get('total_units',0))
        summary['gross_defective_units']=int(summary.get('defective_units',0))
        summary['gross_ski_defect_units_for_report']=int(summary.get('ski_defect_units_for_report',summary.get('defective_units',0)))
        if summary.get('report_trace'):
            summary['_original_report_trace']=deepcopy(summary['report_trace'])
            try:
                saved=json.loads(_path(summary).read_text(encoding='utf-8'))
                if saved.get('binding')==review_binding(summary):summary['report_classifications']=saved.get('decisions',[])
            except (OSError,ValueError):pass
    summary['ot14_policy']=True
    original=summary.get('_original_report_trace')
    if original:
        trace=deepcopy(original);groups=defaultdict(set);all_coords=defaultdict(set);applied=[]
        allocations = _allocate_ski_unknown(summary, original)
        decisions={(d.get('strip_id'),d.get('x'),d.get('y'),d.get('from_code')):d for d in summary.get('report_classifications',[])}
        for group in original.get('groups',[]):
            for x,y in group['coords']:
                all_coords[group['strip_id']].add((x,y))
                d=decisions.get((group['strip_id'],x,y,group['code']))
                code=group['code'];reason=group['reason'];original_reason=''
                if d and code.upper() in REVIEWABLE and re.fullmatch(r'[A-Z]{2}[0-9]{2}',str(d.get('to_code',''))) and str(d.get('note','')).strip():
                    code=d['to_code'];reason='manual_review';applied.append(d)
                elif code.upper() == 'UNKNOWN' and (allocated := allocations.get((group['strip_id'], x, y))):
                    original_reason = reason
                    code, reason = allocated, SKI_UNKNOWN_ALLOCATED
                groups[(group['strip_id'],code,group['process'],reason,original_reason)].add((x,y))
        trace['groups']=[{'strip_id':strip,'code':code,'process':process,'reason':reason,'coords':[list(c) for c in sorted(coords)]}
            | ({'original_reason': original_reason} if original_reason else {})
            for (strip,code,process,reason,original_reason),coords in sorted(groups.items())]
        trace['classifications']=applied;summary['report_trace']=trace
        codes=Counter();processes=Counter();excluded=set()
        for group in trace['groups']:
            codes[group['code']]+=len(group['coords']);processes[group['process']+':'+group['code']]+=len(group['coords'])
            if group['code'].upper()=='OT14':excluded.update((group['strip_id'],*c) for c in group['coords'])
        summary['report_code_distribution']=dict(codes);summary['report_process_code_distribution']=dict(processes)
        ot14=len(excluded)
        excluded_in_ski=0
        for strip,info in original.get('strips',{}).items():
            targets={tuple(c) for c in info.get('ski_coords',[])}
            if not targets or info.get('is_scrap'):targets=all_coords[strip]
            excluded_in_ski+=sum((strip,*coord) in excluded for coord in targets)
        summary['ski_defect_units_for_report']=max(summary['gross_ski_defect_units_for_report']-excluded_in_ski,0)
        summary['report_defect_units_total']=sum(n for c,n in codes.items() if c.upper()!='OT14')
        summary['report_vs_ski_delta']=summary['report_defect_units_total']-summary['ski_defect_units_for_report']
    else:
        ot14=int((summary.get('existing_bbt_code_distribution') or {}).get('OT14',summary.get('total_bbt_units',0)) or 0)
    summary['ot14_excluded_units']=ot14
    summary['dummy_whole_strip_units']=sum(len(g['coords']) for g in summary.get('report_trace',{}).get('groups',[]) if g.get('reason')=='dummy_whole_strip')
    total=summary['gross_total_units']-ot14;ng=summary['gross_defective_units']-ot14
    summary['report_policy_errors']=[] if 0<=ng<=total else ['OT14_EXCLUSION_INVALID']
    summary['report_policy_errors'].extend(summary.get('rework_scope_errors',[]))
    from core.i18n import _
    if summary.get('ivs_transfer_requires_recalculation'):
        summary['report_policy_errors'].append(_('ITS 게더링에서 반영한 뒤 최신 SKI와 공정 TXT를 다시 불러오세요.'))
    awaiting = sorted({row['strip_id'] for row in summary.get('whole_strip_review', [])
                       if row['status'] == 'awaiting_ski'})
    if awaiting:
        summary['report_policy_errors'].append(_('Skipdata를 갱신하고 최신 SKI로 다시 계산하세요.') + ': ' + ', '.join(awaiting))
    if summary.get('whole_strip_unverified'):
        summary['report_policy_errors'].append(_('검증용 TXT를 제외하면 유효한 공정 TXT가 없는 Strip') + ': ' +
                                               ', '.join(summary['whole_strip_unverified']))
    if any(a['reason']=='DUMMY_CODE_CONFLICT' for a in summary.get('dummy_alarms',[])):
        summary['report_policy_errors'].append('DUMMY_CODE_CONFLICT')
    missing=[r['strip_id'] for r in summary.get('inspection_coverage',[]) if r['no_result_block'] and r.get('required_result',True)]
    if missing:summary['report_policy_errors'].append('NO_INSPECTION_RESULT_BLOCK: '+', '.join(missing))
    summary['total_units']=max(total,0);summary['defective_units']=max(ng,0)
    summary['good_units']=max(total-ng,0)
    summary['unit_yield_percent']=round(summary['good_units']/total*100,4) if total>0 else 0.0
    return summary

def classify(summary,rows,target,note):
    target=str(target).strip().upper();note=str(note).strip()
    if not re.fullmatch(r'[A-Z]{2}[0-9]{2}',target):raise ValueError('분류할 정상 불량 코드를 입력하세요. 예: SM03, OT12')
    if not note:raise ValueError('분류 사유를 입력하세요.')
    if sources_changed(summary):raise ValueError('계산 후 원본이 변경되었습니다. 수율을 다시 계산하세요.')
    original=summary.get('_original_report_trace',{})
    valid={(g['strip_id'],x,y,g['code']) for g in original.get('groups',[]) if g['code'].upper() in REVIEWABLE for x,y in g['coords']}
    keys={(r['strip_id'],r['x'],r['y'],'UNKNOWN' if r.get('reason')==SKI_UNKNOWN_ALLOCATED else r['code']) for r in rows}
    if not keys or not keys.issubset(valid):raise ValueError('분류할 미분류 좌표를 선택하세요.')
    decisions=list(summary.get('report_classifications',[]));decisions=[d for d in decisions if (d['strip_id'],d['x'],d['y'],d['from_code']) not in keys]
    for strip,x,y,code in sorted(keys):decisions.append({'strip_id':strip,'x':x,'y':y,'from_code':code,'to_code':target,'note':note,'at':datetime.now(timezone.utc).isoformat()})
    path=_path(summary);path.parent.mkdir(parents=True,exist_ok=True)
    from core.yield_handoff import _write
    _write(path,json.dumps({'binding':review_binding(summary),'decisions':decisions},ensure_ascii=False))
    summary['report_classifications']=decisions
    return prepare_summary(summary)

def unresolved_counts(summary):
    return {code:int(count) for code,count in summary.get('report_code_distribution',{}).items() if code.upper() in UNRESOLVED and count}

def reset_classification(summary):
    from core.yield_handoff import _write
    path=_path(summary);path.parent.mkdir(parents=True,exist_ok=True)
    _write(path,json.dumps({'binding':review_binding(summary),'decisions':[]},ensure_ascii=False))
    summary['report_classifications']=[]
    return prepare_summary(summary)

def export_issues(summary):
    from core.i18n import _
    errors=[]
    rows=mismatch_rows(summary)
    if rows:errors.append(_('불일치 Strip')+': '+str(len(rows)))
    pending=unresolved_counts(summary)
    if pending:errors.append(_('미분류 코드')+': '+', '.join(f'{c}: {n}' for c,n in pending.items()))
    errors.extend(summary.get('report_policy_errors',[]))
    if sources_changed(summary):errors.append(_('계산 후 원본이 변경되었습니다. 수율을 다시 계산하세요.'))
    return errors
