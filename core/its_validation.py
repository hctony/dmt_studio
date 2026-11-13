"""Coordinate-first ITS validation. Inputs must be current, verified histories.

9998 is pending VRS review, never a confirmed defect. No source data is edited.
An absent SKI Strip entry is zero only when full-file read/scope is verified.
"""


def validate_strip(processes, bbt_coords, ski_coords, *, history_verified=False,
                   ski_scope_verified=False, inspection_times=None, ski_time=None,retired_coords=(),epoch_start=None):
    confirmed={};pending=set()
    for stage in ('IVS','VRS','FVI'):
        by_code=processes.get(stage,{})
        confirmed[stage]=set().union(*(set(coords) for code,coords in by_code.items()
                                      if code not in ('','0','9998'))) if by_code else set()
        if stage=='IVS':pending.update(by_code.get('9998',()))
    # Missing 9998 in a VRS export is not proof of a false positive unless that
    # Strip's later VRS review (including an empty result) is independently known.
    expected=set(bbt_coords).union(*confirmed.values())
    ski=set(ski_coords);missing_source=ski-expected;pending_ski=expected-ski
    times=inspection_times or {}
    newer_stages=sorted(stage for stage,stamp in times.items() if stamp and ski_time and stamp>ski_time)
    if not history_verified or not ski_scope_verified:
        status='HISTORY_UNVERIFIED'
    elif missing_source and set(missing_source).issubset(set(retired_coords)) and ski_time and epoch_start and ski_time<epoch_start:
        status='SKI_REFRESH_REQUIRED'
    elif missing_source and pending_ski:status='COORDINATE_MISMATCH'
    elif missing_source:status='SOURCE_MISSING'
    elif pending_ski:status='SKI_REFRESH_REQUIRED'
    else:status='PASS'
    return {'status':status,'auto_pass':status=='PASS','expected_count':len(expected),
            'ski_count':len(ski),'expected_coords':sorted(expected),
            'missing_source_coords':sorted(missing_source),'ski_update_coords':sorted(pending_ski),
            'newer_than_ski_stages':newer_stages,'ivs_9998_coords':sorted(pending),
            'confirmed_by_process':{stage:sorted(coords) for stage,coords in confirmed.items()}}


def overlapping_history_dates(items):
    """Different dates alone are not rework: only repeated Strip IDs need review."""
    from datetime import datetime
    dates={}
    for item in items:
        dates.setdefault(item.strip_id.upper(),set()).add(datetime.fromtimestamp(item.source_mtime).date())
    return {strip:sorted(str(day) for day in days) for strip,days in dates.items() if len(days)>1}


def load_history(paths):
    import json
    grouped={}
    for path in paths:
        payload=json.loads(path.read_text(encoding='utf-8-sig'))
        manifest=payload.get('strip_history',{})
        if manifest.get('schema')!='dmt-its-history-v1':continue
        if manifest.get('complete') is False:raise ValueError('IVS 검사 이력이 불완전합니다. IVS 전체 수집을 다시 실행하세요.')
        for event in manifest.get('events',[]):
            if event.get('strip_id'):grouped.setdefault(event['strip_id'].upper(),[]).append(event)
    result={}
    for strip,events in grouped.items():
        usable=[e for e in events if isinstance(e.get('source_mtime'),(int,float)) and e.get('machine') and e.get('trial')]
        if len(usable)!=len(events) or not usable:continue
        latest=max(e['source_mtime'] for e in usable)
        heads=[e for e in usable if e['source_mtime']==latest]
        if len({(e['machine'],e['trial']) for e in heads})!=1:continue
        head=heads[0];epoch=[e for e in usable if (e['machine'],e['trial'])==(head['machine'],head['trial'])]
        result[strip]={**head,'epoch_start':min(e['source_mtime'] for e in epoch),'events':events}
    if grouped and not result:raise ValueError('설비·Trial 시각을 확인할 수 없어 최신 검사 이력을 확정할 수 없습니다.')
    return result


def source_metadata(path,strip):
    """Generated exports carry original per-Strip clocks, bound to file content."""
    import hashlib,json
    from pathlib import Path
    path=Path(path)
    sidecar=path.with_suffix(path.suffix+'.its.json')
    if sidecar.is_file():
        data=json.loads(sidecar.read_text(encoding='utf-8'))
        if hashlib.sha256(path.read_bytes()).hexdigest()!=data.get('sha256'):return None
        return data.get('strips',{}).get(strip)
    return {'source_mtime':path.stat().st_mtime,'sources':[str(path)]}


def source_clock_map(path):
    import hashlib,json
    sidecar=path.with_suffix(path.suffix+'.its.json')
    if sidecar.is_file():
        data=json.loads(sidecar.read_text(encoding='utf-8'))
        if hashlib.sha256(path.read_bytes()).hexdigest()!=data.get('sha256'):return {}
        return data.get('strips',{})
    return None


def epoch_candidate(current,candidate,bbt,ski,ski_time):
    """Only merge partial outputs inside a verified epoch with fresh exact SKI."""
    from core.gather import merge_gather_strips,gather_line_coords
    latest=candidate if candidate.source_mtime>=current.source_mtime else current
    merged=merge_gather_strips(current,candidate)
    if (latest.defect_lines and ski and ski_time>=merged.source_mtime
            and gather_line_coords(merged.defect_lines)|set(bbt)==set(ski)):
        return merged
    return latest


def format_audits(report):
    from core.i18n import _
    names={'PASS':_('정상 일치'),'HISTORY_UNVERIFIED':_('검사 이력 확인 필요'),
           'SOURCE_MISSING':_('소스 좌표 누락'),'SKI_REFRESH_REQUIRED':_('SKI 갱신 필요'),
           'COORDINATE_MISMATCH':_('좌표 불일치')}
    rows=report.get('strips',[])
    lines=[_('ITS 최신 이력 검증')+f": {sum(r['auto_pass'] for r in rows)} / {len(rows)} PASS"]
    for row in [r for r in rows if not r['auto_pass']][:100]:
        lines.append(f"{row['strip_id']} · {row['machine']}/{row['trial']} · "+names[row['status']])
        for key,label in [('missing_source_coords',_('SKI에만 있는 좌표')),('ski_update_coords',_('SKI 갱신 대상 좌표'))]:
            if row[key]:lines.append(label+': '+', '.join(f'X:{x:03d} Y:{y:03d}' for x,y in row[key]))
    if report.get('untracked_strips'):lines.append(_('XML 이력 없는 Strip')+': '+', '.join(report['untracked_strips'][:100]))
    return '\n'.join(lines)
