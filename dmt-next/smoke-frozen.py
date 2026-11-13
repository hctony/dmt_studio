"""Exercise the bundled backend protocol after packaging, without source imports."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import queue
import threading
import time

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

# Exercise lazy imports and the asynchronous evidence reader in the frozen app,
# not just the source interpreter. No production NAS connection is required.
with tempfile.TemporaryDirectory() as data:
    fixture=Path(data)
    txt=fixture/'gather.txt';txt.write_text('1,TEST-,TEST-01\nTEST-,TEST-01,1,1,T,1111\nTEST-,TEST-01,EOS\n')
    ski=fixture/'final.ski';ski.write_text('TEST-01,1,1\nTEST-01,2,1\n')
    xml=fixture/'original.xml';xml.write_text('<Root barcode="TEST-01"><Unit><P0 unit_idx="0" ivs_1="1111"/></Unit></Root>')
    ivs=fixture/'ivs.json';ivs.write_text(json.dumps({'schema':'dmt-studio-ivs-v1','lot_id':'TEST-','model':'M','records':[{'strip_id':'TEST-01','machine':'A','model':'M','lot':'TEST-','trial':'T','strip_folder':'1','container':'Unit','face':'T','unit_idx':'0','img_merge_idx':'0','ivs_1':'1111','code_main':'1111','code_sub':'','def_side':'F','strip_pt_x':1,'strip_pt_y':1,'img_pt_x':0,'img_pt_y':0,'source_xml':str(xml),'images':[]}]}))
    process=subprocess.Popen([str(backend),'--gui'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8',env=dict(os.environ,DMT_DATA_DIR=data,QT_QPA_PLATFORM='offscreen'),cwd=data)
    output=queue.Queue()
    def read_output():
        for line in process.stdout:output.put(line)
    threading.Thread(target=read_output,daemon=True).start()
    sequence=0
    def rpc(method,**args):
        global sequence
        sequence+=1;request_id=sequence
        process.stdin.write(json.dumps({'id':request_id,'method':method,'args':args})+'\n');process.stdin.flush()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            message=json.loads(output.get(timeout=max(.1,deadline-time.monotonic())))
            if message.get('id')==request_id:
                assert not message.get('error'),message
                return message['result']
        raise TimeoutError(method)
    def walk(widget):
        if not widget:return []
        return [widget]+[item for child in widget.get('children',[]) for item in walk(child)]+walk(widget.get('content'))
    try:
        state=rpc('gui.snapshot');rpc('gui.action',id=state['root']['id'],action='tab',value=6)
        state=rpc('gui.snapshot');inputs=[w for w in walk(state['root']) if w['type']=='input']
        assert len(inputs)==11
        for widget,value in zip(inputs,['M',str(txt),'','',str(ivs),'','','',str(ski),'4','3']):
            rpc('gui.action',id=widget['id'],action='input',value=value)
        buttons={w['text']:w for w in walk(rpc('gui.snapshot')['root']) if w['type']=='button'}
        rpc('gui.action',id=buttons['수율 계산']['id'],action='click')
        rpc('gui.action',id=buttons['코드 미리보기']['id'],action='click')
        state=rpc('gui.snapshot');dialog=state['dialogs'][0]
        table=next(w for w in walk(dialog['content']) if w['type']=='table' and w['headers'][0]=='성적서 코드')
        assert [r[:2] for r in table['rows']]==[['UNKNOWN','1'],['1111','1']]
        rpc('gui.action',id=table['id'],action='table',row=1,column=0)
        for attempt in range(100):
            state=rpc('gui.snapshot')
            texts=[w['text'] for w in walk(state['dialogs'][0]['content']) if w['type']=='text']
            if any('ivs_1="1111"' in text for text in texts):break
            time.sleep(.05)
        assert any('ivs_1="1111"' in text for text in texts),texts
        assert any('▶ L1:' in text for text in texts),texts
        rpc('gui.action',id=dialog['id'],action='reject')
        for attempt in range(100):
            if not rpc('gui.snapshot')['busy']:break
            time.sleep(.05)
        print('Packaged backend: report code counts, TXT/SKI/XML evidence and source worker OK')
    finally:
        process.stdin.close()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        error=process.stderr.read()
        assert process.returncode==0,error
