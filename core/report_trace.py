"""Trace actual report assignments, grouped to avoid one dictionary per Unit."""
from collections import Counter,defaultdict
from pathlib import Path
from core.report_codes import excel_code

REASONS={
 'bbt':'BBT 좌표를 OT14로 배정',
 'dummy9999':'IVS Dummy 9999 양폐: BBT 제외 나머지 좌표',
 'gather':'선택된 공정 TXT 코드',
 'missing_code':'TXT 불량 코드가 비어 있어 UNKNOWN으로 배정',
 'ivs_recovery':'IVS JSON 기록으로 170 코드 보정',
 'ski_unassigned':'SKI 불량 좌표에 대응하는 불량 코드를 찾지 못함',
 'xout_fill':'양폐 Strip의 미배정 좌표를 170으로 보충',
}


def file_version(path):
    try:
        stat=Path(path).stat()
        return [stat.st_mtime_ns,stat.st_size]
    except OSError:
        return None


class ReportTrace:
    def __init__(self,process_inputs,ivs_json_paths,skipdata_path,ski_path):
        self.groups=defaultdict(set)
        self.strips={}
        self.inputs={key:[str(p) for p in paths] for key,paths in process_inputs.items()}
        self.ivs_json=[str(p) for p in ivs_json_paths]
        self.bbt_folder=str(skipdata_path) if skipdata_path else ''
        self.ski=str(ski_path) if ski_path else ''
        files=[p for paths in self.inputs.values() for p in paths]+self.ivs_json+([self.ski] if self.ski else [])
        self.versions={p:file_version(p) for p in files}
        self.bbt_sources={}

    def set_bbt_sources(self,sources):
        self.bbt_sources=sources
        self.versions.update({p:file_version(p) for paths in sources.values() for p in paths})

    def add(self,strip,code,process,coord,reason):
        self.groups[(strip,code,process,reason)].add(coord)

    def distributions(self):
        codes=Counter();processes=Counter()
        for (_,code,process,_),coords in self.groups.items():
            codes[code]+=len(coords);processes[process+':'+code]+=len(coords)
        return dict(sorted(codes.items())),dict(sorted(processes.items()))

    def snapshot(self):
        return {'version':1,'groups':[{'strip_id':strip,'code':code,'process':process,'reason':reason,
                    'coords':[list(c) for c in sorted(coords,key=lambda c:(c[1],c[0]))]}
                for (strip,code,process,reason),coords in sorted(self.groups.items())],
                'strips':self.strips,'inputs':self.inputs,'ivs_json_paths':self.ivs_json,
                'bbt_folder':self.bbt_folder,'bbt_sources':self.bbt_sources,'ski_path':self.ski,'file_versions':self.versions}


def matching_groups(trace,code):
    return [group for group in trace.get('groups',[]) if excel_code(group['code'])==code]


def trace_rows(trace,code,offset=0,limit=200):
    result=[];index=0
    for group in matching_groups(trace,code):
        for x,y in group['coords']:
            if index>=offset and len(result)<limit:
                result.append({**{key:value for key,value in group.items() if key!='coords'},'x':x,'y':y})
            index+=1
    return result,index
