"""Reproducible NAS latency simulation; does not contact a production NAS."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from test_nas_performance import LocalSftp
from core.nas_cache import XmlStore,ScanSftp
from core.ivs import parse_ivs_xml,build_summary
from core.models import TrialContext

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);file=root/'trial/1/one_ivs.xml';file.parent.mkdir(parents=True)
    count=160;latency=.002
    file.write_text('<Root barcode="RX-01"><Unit>'+''.join(f'<P{i} ivs_1="SM03" unit_idx="{i}"/>' for i in range(count))+'</Unit></Root>')
    os.utime(file,(1000000000,1000000000));ctx=TrialContext('A','M','RX-','T','/trial')
    results=[];summaries=[]
    for scenario in ('missing_images','bmp_present'):
        if scenario=='bmp_present':
            for i in range(count):
                folder=root/f'trial/1/Unit/U{i}/FC';folder.mkdir(parents=True)
                for suffix in ('_Sub2','','_Sub1'):(folder/('p0'+suffix+'.bmp')).write_bytes(b'image')
        for mode in ('previous','first_scan','repeat_scan'):
            raw=LocalSftp(root,latency)
            client=raw if mode=='previous' else ScanSftp(raw,XmlStore('benchmark-'+scenario,root/'cache'))
            if mode!='previous':client._dmt_lazy_images=True
            started=time.perf_counter();records=parse_ivs_xml('/trial/1/one_ivs.xml',client,ctx,'1')
            duration=time.perf_counter()-started;summaries.append(build_summary(records,192))
            results.append({'scenario':scenario,'mode':mode,'seconds':round(duration,4),'remote_open_calls':raw.reads,'remote_stat_calls':raw.stats,'remote_directory_calls':raw.lists,'records':len(records)})
            client.close()
    assert all(summary==summaries[0] for summary in summaries)
    report={'kind':'simulated_sftp_latency','latency_seconds_per_operation':latency,'defect_records':count,'summary_equal':True,'runs':results}
    if len(sys.argv)>1:Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
