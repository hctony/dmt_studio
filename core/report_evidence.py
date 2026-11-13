"""Read-only comparison helpers. Frozen assignments and live files stay distinct."""
import json
import os
import re
from pathlib import Path
from core.gather import parse_xy_from_gather_line
from core.report_codes import excel_code
from core.report_trace import REASONS,file_version
from core.utils import unit_idx_to_xy
from core.i18n import _


def local_allowed(path):
    if os.environ.get('DMT_REMOTE_SESSION')=='1':
        from remote_paths import allowed_path
        if not allowed_path(path):
            raise PermissionError(_("A PC에서 허용한 데이터 폴더를 선택하세요."))


def matching_refs(trace,row):
    info=trace.get('strips',{}).get(row['strip_id'],{})
    if row['reason']=='ivs_recovery':
        exact=info.get('recovered_ivs',{}).get(f"{row['x']},{row['y']}")
        if exact:
            return [exact]
    refs=[]
    for ref in info.get('ivs_records',[]):
        if row['reason']=='dummy9999':
            match=(ref[2]=='Dummy' or ref[0]=='-1') and ref[1]=='9999'
        else:
            match=unit_idx_to_xy(ref[0],info.get('x_count',0))==(row['x'],row['y'])
        if match:
            refs.append(ref)
    return refs


def local_candidates(trace,row):
    info=trace.get('strips',{}).get(row['strip_id'],{})
    result=[]
    processes=info.get('processes',{})
    bbt=trace.get('bbt_sources',{}).get(row['strip_id'],[])
    if row['process']=='BBT':result.extend(bbt)
    preferred=processes.get(row['process'],{}).get('selected_source','')
    if preferred:
        result.append(preferred)
    for process,data in processes.items():
        if data.get('selected_source'):
            result.append(data['selected_source'])
        result.extend(data.get('sources',[]))
    result.extend(bbt)
    if trace.get('bbt_folder') and not bbt:
        result.append(str(Path(trace['bbt_folder'])/(row['strip_id']+'.rpt')))
    return list(dict.fromkeys(result))


def frozen_text(trace,row):
    info=trace.get('strips',{}).get(row['strip_id'],{})
    coord=[row['x'],row['y']]
    lines=[_('계산 시점의 배정 근거'),f"StripID: {row['strip_id']}",f"X: {row['x']:03d} / Y: {row['y']:03d}",
        _('성적서 코드')+': '+excel_code(row['code']),_('집계 원본 코드')+': '+row['code'],
        _('배정 공정')+': '+row['process'],_('계산 사유')+': '+_(REASONS.get(row['reason'],row['reason'])),
        _('계산에 사용한 X 폭')+': '+str(info.get('x_count',0)),
        f"SKI: {coord in info.get('ski_coords',[])} / BBT: {coord in info.get('bbt_coords',[])}",
        _('좌표 환산 Unit index (0 기준)')+': '+str((row['y']-1)*info.get('x_count',0)+row['x']-1)]
    for process,data in info.get('processes',{}).items():
        matches=[line for line in data.get('defect_lines',[]) if parse_xy_from_gather_line(line)==tuple(coord)]
        lines+=['',f'[{process}]',_('계산에 선택된 파일')+': '+data.get('selected_source','')]
        lines+=matches or [_('해당 좌표의 TXT 기록 없음')]
    refs=matching_refs(trace,row)
    lines+=['',_('계산 시점 IVS JSON 기록')]
    for ref in refs:
        lines+=[f'JSON: {ref[5]}',json.dumps(dict(zip(['unit_idx','ivs_1','container','face','source_xml','source_json','code_main','code_sub'],ref)),ensure_ascii=False)]
    if not refs:
        lines.append(_('해당 Unit의 IVS JSON 기록 없음'))
    return '\n'.join(lines)


def text_encoding(path):
    with Path(path).open('rb') as file:
        head=file.read(4096)
    if head.startswith((b'\xff\xfe',b'\xfe\xff')):
        return 'utf-16'
    try:
        import codecs
        codecs.getincrementaldecoder('utf-8-sig')().decode(head,final=False);return 'utf-8-sig'
    except UnicodeDecodeError:
        return 'cp949'


def read_text_matches(path,strip,coord,kind,check=lambda:None):
    local_allowed(path);hits=[];current=Path(path).stem.upper()
    with Path(path).open('r',encoding=text_encoding(path),errors='replace') as file:
        for number,raw in enumerate(file,1):
            check();line=raw.strip();parts=[value.strip() for value in line.split(',')]
            if kind=='rpt':
                if line.startswith('[') and line.endswith(']'):
                    current=line[1:-1].strip().upper()
                match=re.search(r'X\s*:\s*(\d+)\s+Y\s*:\s*(\d+)',line,re.I)
                selected=current==strip and match and (int(match[1]),int(match[2]))==coord
            elif kind=='ski':
                selected=len(parts)>=3 and parts[0].upper()==strip
            else:
                selected=len(parts)>=6 and parts[1].upper()==strip and parse_xy_from_gather_line(line)==coord
            if selected:
                exact=kind!='ski' or len(parts)>=3 and parts[1:3]==[str(coord[0]),str(coord[1])]
                # Compare numeric coordinates even when the SKI is zero-padded.
                if kind=='ski':
                    try:exact=(int(parts[1]),int(parts[2]))==coord
                    except ValueError:exact=False
                hits.append(('▶ ' if exact else '  ')+f'L{number}: '+raw.rstrip())
                if len(hits)>=200:
                    hits.append(_('표시 상한 200줄에 도달했습니다.'));break
    return hits


def live_text(trace,path,strip,coord,kind,check=lambda:None):
    before=trace.get('file_versions',{}).get(str(path));current=file_version(path)
    state=_('계산 후 파일 변경됨') if before is not None and current!=before else _('계산 시점과 파일 크기·수정 시각 일치') if before is not None else _('계산 시점 버전 미기록')
    header=f'{path}\n{state}'
    hits=read_text_matches(path,strip,coord,kind,check)
    return header+'\n'+('\n'.join(hits) if hits else _('해당 Strip·좌표의 원문 없음'))


def xml_excerpt(data,row,info):
    from core.nas import decode_xml
    root=decode_xml(data)
    wanted=str((row['y']-1)*info.get('x_count',0)+row['x']-1)
    matches=[]
    import xml.etree.ElementTree as E
    for parent in root.iter():
        for element in parent:
            if not element.tag.split('}')[-1].upper().startswith('P'):
                continue
            dummy=parent.tag.split('}')[-1].lower()=='dummy' or element.attrib.get('unit_idx')=='-1'
            match=dummy and element.attrib.get('ivs_1')=='9999' if row['reason']=='dummy9999' else element.attrib.get('unit_idx')==wanted
            if match:
                matches.append(E.tostring(element,encoding='unicode')[:12000])
    barcodes=sorted({e.attrib['barcode'] for e in root.iter() if e.attrib.get('barcode')})
    text='barcode: '+', '.join(barcodes)+'\nUnit index: '+wanted+'\n'
    if barcodes and row['strip_id'].upper() not in {b.upper() for b in barcodes}:
        text+=_('주의: XML barcode와 선택 StripID가 다릅니다.')+'\n'
    return text+('\n'.join(matches[:20]) if matches else _('해당 Unit의 XML 항목 없음'))
