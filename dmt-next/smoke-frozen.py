"""Exercise the bundled backend protocol after packaging, without source imports."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parent
backend = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'backend-dist/dmt-service/dmt-service.exe'
for relative in ['_internal/shiboken6/Shiboken.pyd','_internal/shiboken6/shiboken6.abi3.dll','_internal/PySide6/QtCore.pyd','_internal/PySide6/QtGui.pyd','_internal/PySide6/QtWidgets.pyd']:
    assert (backend.parent/relative).is_file(), relative
assert (backend.parent/'_internal/resources/templates/report.xlsx').is_file(), 'Bundled report template missing'
with tempfile.TemporaryDirectory() as data:
    requests = [{'id':1,'method':'gui.snapshot'}, {'id':2,'method':'workflow.status'},
        {'id':3,'method':'gui.import-models','args':{'data':{'PACKAGED-TEST':{'units_per_strip':'88','xout_limit':'18','vrs_txt_paths':r'Z:\Taiwan\LOT.txt','ski_path':''}}}},
        {'id':4,'method':'gui.models'}, {'id':5,'method':'gui.resource-check'}]
    result = subprocess.run([str(backend),'--gui'],
        input=''.join(json.dumps(r)+'\n' for r in requests), text=True, encoding='utf8',
        capture_output=True, env=dict(os.environ,DMT_DATA_DIR=data,QT_QPA_PLATFORM='offscreen'),cwd=data,timeout=45)
    assert result.returncode == 0, result.stderr
    messages = {r['id']:r for line in result.stdout.splitlines() if (r:=json.loads(line)).get('id')}
    assert len(messages[1]['result']['root']['labels']) == 10, messages
    assert messages[2]['result']['successfulLots'] == 0, messages
    assert messages[2]['result']['preferences']['autofill'] is False, messages
    assert messages[3]['result']['count'] == 1, messages
    assert messages[4]['result']['selected'] == 'PACKAGED-TEST', messages
    assert messages[5]['result']['rssBytes'] > 0, messages
    saved = json.loads((Path(data)/'gui-inputs.json').read_text(encoding='utf-8'))
    assert saved['6/YieldTab:/xout_limit_in'] == '18', saved
    assert saved['6/YieldTab:/vrs_txt_in'] == r'Z:\Taiwan\LOT.txt', saved
    print('Packaged backend: 10 tabs, LOT workflow, original model JSON import OK')
