"""CSV audit of unique OT14 coordinates and every original RPT line."""
from collections import Counter
import csv
from pathlib import Path
from core.skipdata import RPT_STRIP_RE, RPT_XY_RE, RPT_CODE_RE
from core.report_trace import file_version
from core.inspection_history import decode_original


def manifest(folder):
    if not folder or not Path(folder).is_dir():return {}
    return {str(p):file_version(p) for p in sorted(Path(folder).glob('*.rpt')) if p.is_file()}


def rows(trace):
    folder=trace.get('bbt_folder','')
    versions=trace.get('rpt_audit_versions')
    if versions is None or manifest(folder)!=versions:raise ValueError('RPT_AUDIT_CHANGED_RECALCULATE')
    selected=set(trace.get('selected_strip_ids',trace.get('strips',{})))
    final={(g['strip_id'],x,y) for g in trace.get('groups',[]) if g['code'].upper()=='OT14' for x,y in g['coords']}
    raw=[];counts=Counter();sources={}
    for filename,version in versions.items():
        from os import environ
        if environ.get('DMT_REMOTE_SESSION')=='1':
            from remote_paths import allowed_path
            if not allowed_path(filename):raise PermissionError('RPT_AUDIT_SOURCE_NOT_ALLOWED')
        path=Path(filename);body=path.read_bytes()
        if file_version(path)!=version:raise ValueError('RPT_AUDIT_CHANGED_RECALCULATE')
        strip=path.stem.strip().upper()
        for number,line in enumerate(decode_original(body).splitlines(),1):
            header=RPT_STRIP_RE.match(line.strip())
            if header:strip=header.group(1).strip().upper()
            xy=RPT_XY_RE.search(line);code=RPT_CODE_RE.search(line)
            coord=(strip,int(xy.group(1)),int(xy.group(2))) if xy else None
            state='PARSED_COORDINATE' if coord else 'HEADER' if header else 'UNPARSED_COORDINATE' if 'X:' in line.upper() or 'Y:' in line.upper() else 'OTHER_LINE'
            if coord:
                counts[coord]+=1;sources.setdefault(coord,set()).add(filename)
            raw.append(dict(row_type='RPT_RAW',strip=strip,x=coord[1] if coord else '',y=coord[2] if coord else '',
                selected_strip=int(strip in selected),final_ot14=int(coord in final) if coord else '',
                source=filename,line_number=number,raw_line=line,original_code=code.group(1) if code else '',parse_status=state))
    output=[]
    for coord in sorted(set(counts)|final):
        strip,x,y=coord
        info=trace.get('strips',{}).get(strip,{})
        output.append(dict(row_type='OT14_COORDINATE',strip=strip,x=x,y=y,selected_strip=int(strip in selected),
            final_ot14=int(coord in final),rpt_occurrences=counts[coord],duplicate_rows=max(counts[coord]-1,0),
            in_ski=int([x,y] in info.get('ski_coords',[])),source='; '.join(sorted(sources.get(coord,set()))),
            parse_status='ASSIGNED' if coord in final else 'OUTSIDE_SELECTED_STRIPS' if strip not in selected else 'NOT_ASSIGNED_CHECK'))
    for row in raw:
        if row['x']!='':row['rpt_occurrences']=counts[(row['strip'],row['x'],row['y'])]
    if manifest(folder)!=versions:raise ValueError('RPT_AUDIT_CHANGED_RECALCULATE')
    return output+raw


def export_csv(trace, target):
    data=rows(trace)
    fields=['row_type','strip','x','y','selected_strip','final_ot14','rpt_occurrences','duplicate_rows',
            'in_ski','source','line_number','original_code','parse_status','raw_line']
    def cell(value):
        # Preserve original text visibly while preventing Excel formula execution.
        return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value
    with Path(target).open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
        for row in data:writer.writerow({k:cell(v) for k,v in row.items()})
    return len(data)
