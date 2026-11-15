import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from workers.gather_workers import GatheringWorker,StripGatherSearchThread
from unittest.mock import patch

app=QApplication([])
with tempfile.TemporaryDirectory() as directory:
    pc=Path(directory)/'pc1';pc.mkdir()
    (pc/'gather.txt').write_text('1,TEST-,TEST-01\nTEST-,TEST-01,1,1,T,SM03\nTEST-,TEST-01,EOS\n',encoding='utf-8')
    from core.collection import collect,write_export,reports_for_inputs
    parsed,first=collect([pc],30,'TEST-','VRS',lambda:False,print)
    assert first['complete'] and parsed[pc][0][1].strip_id=='TEST-01'
    worker=GatheringWorker('VRS','','','TEST','TEST-',2,2,3,'170',set(),set())
    results=[];errors=[];worker.finished.connect(lambda *result:results.append(result));worker.failed.connect(errors.append)
    with patch.object(worker,'build_paths',return_value=[pc]):worker.run()
    assert not errors and results and results[0][2]['collection']['complete']
    assert 'SM03' in results[0][1]
    search=StripGatherSearchThread('TEST-01','TEST');hits=[];search.finished.connect(hits.extend);search.failed.connect(errors.append)
    with patch.object(search,'build_paths',return_value=[pc]):search.run()
    assert not errors and len(hits)==3 and all(hit['coord_count']==1 for hit in hits)
    _,warm=collect([pc],30,'OTHER-','VRS',lambda:False,print)
    assert warm['complete'] and warm['hosts'][0]['reused']==1 and not warm['hosts'][0]['matched_strips']
    _,partial=collect([pc,Path(directory)/'offline'],30,'TEST-','VRS',lambda:False,print)
    target=Path(directory)/'draft.txt';write_export(target,'1,TEST-,TEST-01\nTEST-,TEST-01,EOS\n',{},partial['receipt'])
    guard=reports_for_inputs([target],{'VRS':[target]})
    assert guard['enabled'] and not guard['complete'] and len(guard['incomplete_hosts'])==1
    assert Path(parsed[pc][0][1].sources[1]).is_file()
    print('Real Go collection / warm LOT lookup / offline-host receipt passed')
