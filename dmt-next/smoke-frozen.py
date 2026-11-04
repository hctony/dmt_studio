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
with tempfile.TemporaryDirectory() as data:
    requests = [{'id':1,'method':'gui.snapshot'}, {'id':2,'method':'workflow.status'}]
    result = subprocess.run([str(backend),'--gui'],
        input=''.join(json.dumps(r)+'\n' for r in requests), text=True, encoding='utf8',
        capture_output=True, env=dict(os.environ,DMT_DATA_DIR=data,QT_QPA_PLATFORM='offscreen'),cwd=data,timeout=45)
    assert result.returncode == 0, result.stderr
    messages = {r['id']:r for line in result.stdout.splitlines() if (r:=json.loads(line)).get('id')}
    assert len(messages[1]['result']['root']['labels']) == 10, messages
    assert messages[2]['result']['successfulLots'] == 0, messages
    assert messages[2]['result']['preferences']['autofill'] is False, messages
    print('Packaged backend: 10 tabs and LOT workflow OK')
