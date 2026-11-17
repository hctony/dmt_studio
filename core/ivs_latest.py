"""Select one inspection occurrence per XML barcode, retaining split XML views."""
from collections import defaultdict
from dataclasses import astuple
import math

def epoch_key(event):
    return (str(event.get('machine','')),str(event.get('trial','')),str(event.get('strip_folder','')))

def select_latest(records,events):
    by_strip=defaultdict(list)
    for event in events:
        strip=str(event.get('strip_id','')).strip().upper()
        if strip and strip!='UNKNOWN':by_strip[strip].append(event)
    selected={};issues=[];replaced=0
    for strip,items in by_strip.items():
        occurrences=defaultdict(list)
        for event in items:occurrences[epoch_key(event)].append(event)
        usable=[e for e in items if isinstance(e.get('source_mtime'),(int,float)) and math.isfinite(e['source_mtime'])]
        if len(usable)!=len(items):issues.append({'strip_id':strip,'reason':'SOURCE_TIME_UNAVAILABLE'})
        if usable:
            latest=max(e['source_mtime'] for e in usable)
            heads=[e for e in usable if e['source_mtime']==latest]
        else:heads=items
        keys={epoch_key(e) for e in heads}
        if len(keys)>1:issues.append({'strip_id':strip,'reason':'SOURCE_TIME_TIE'})
        # Stable fallback is inspection-needed, never treated as a verified clock.
        key=max(keys);head=max((e for e in heads if epoch_key(e)==key),key=lambda e:str(e.get('source_xml','')))
        if any(e.get('parse_failed') for e in occurrences[key]):issues.append({'strip_id':strip,'reason':'LATEST_XML_UNREADABLE'})
        selected[strip]={**head,'epoch_start':min((e['source_mtime'] for e in occurrences[key] if e in usable),default=None)}
        replaced+=max(len(occurrences)-1,0)
    result=[];seen=set()
    for record in records:
        strip=record.strip_id.strip().upper();head=selected.get(strip)
        if head is None:
            if strip not in by_strip:issues.append({'strip_id':strip,'reason':'BARCODE_HISTORY_MISSING'})
            continue
        if (record.machine,record.trial)!=(head['machine'],head['trial']):continue
        if head.get('strip_folder') and record.strip_folder!=head['strip_folder']:continue
        # The same physical occurrence can contain front/back or other split XMLs.
        identity=astuple(record);identity=identity[:-1]+(tuple(identity[-1]),)
        if identity in seen:continue
        seen.add(identity);result.append(record)
    unique_issues=[dict(pair) for pair in dict.fromkeys(tuple(sorted(issue.items())) for issue in issues)]
    return result,{'schema':'dmt-ivs-latest-v1','clock_basis':'source_file_mtime','complete':not unique_issues,
        'selected':selected,'replaced_occurrences':replaced,'issues':unique_issues}
