"""Exercise the bundled backend protocol after packaging, without source imports."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as data:
    requests = [{'id':1,'method':'gui.snapshot'}, {'id':2,'method':'workflow.status'}]
    result = subprocess.run([str(root/'backend-dist/dmt-service/dmt-service.exe'),'--gui'],
        input=''.join(json.dumps(r)+'\n' for r in requests), text=True, encoding='utf8',
        capture_output=True, env=dict(os.environ,DMT_DATA_DIR=data,QT_QPA_PLATFORM='offscreen'),cwd=data,timeout=45)
    assert result.returncode == 0, result.stderr
    messages = {r['id']:r for line in result.stdout.splitlines() if (r:=json.loads(line)).get('id')}
    assert len(messages[1]['result']['root']['labels']) == 10, messages
    assert messages[2]['result']['successfulLots'] == 0, messages
    assert messages[2]['result']['preferences']['autofill'] is False, messages
    print('Packaged backend: 10 tabs and LOT workflow OK')
