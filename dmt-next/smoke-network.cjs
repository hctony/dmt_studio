/* Three real remote backends plus an actual browser client and local mode panel. */
const {app,BrowserWindow}=require('electron'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),net=require('node:net');
const root=path.join(__dirname,'smoke-user-data','network-'+Date.now());fs.mkdirSync(root,{recursive:true});const sources=path.join(root,'sources');fs.mkdirSync(sources);const tokens=['B','C','D'].map(name=>({name,token:name.repeat(64)}));
fs.writeFileSync(path.join(root,'network-server.json'),JSON.stringify({enabled:false,host:'127.0.0.1',port:8765,concurrency:2,roots:[sources],clients:tokens}));
const txt=path.join(sources,'gather.txt'),ids=path.join(sources,'strips.json');fs.writeFileSync(txt,'1,TEST-,TEST-01\nTEST-,TEST-01,1,1,T,SM03\nTEST-,TEST-01,EOS\n');fs.writeFileSync(ids,JSON.stringify({lot_id:'TEST-',strip_ids:['TEST-01']}));
const auditTxt=path.join(sources,'audit.txt'),auditSki=path.join(sources,'final.ski'),auditXml=path.join(sources,'수율 계산.xml'),auditJson=path.join(sources,'audit.json');
fs.writeFileSync(auditTxt,'1,TEST-,TEST-01\nTEST-,TEST-01,1,1,T,1111\nTEST-,TEST-01,EOS\n');fs.writeFileSync(auditSki,'TEST-01,1,1\nTEST-01,2,1\n');
fs.writeFileSync(auditXml,'<Root barcode="TEST-01"><Unit><P0 unit_idx="0" ivs_1="1111" note="단일 불량률"/></Unit></Root>');
fs.writeFileSync(auditJson,JSON.stringify({schema:'dmt-studio-ivs-v1',model:'TEST',lot_id:'TEST-',records:[{strip_id:'TEST-01',machine:'A',model:'TEST',lot:'TEST-',trial:'T',strip_folder:'1',container:'Unit',face:'T',unit_idx:'0',img_merge_idx:'0',ivs_1:'1111',code_main:'1111',code_sub:'',def_side:'F',strip_pt_x:1,strip_pt_y:1,img_pt_x:0,img_pt_y:0,source_xml:auditXml,images:[]}]}));
app.setPath('userData',root);process.env.DMT_HEADLESS='1';process.env.DMT_SETTINGS_FILE=path.join(root,'local.ini');app.disableHardwareAcceleration();require('./desktop');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const deadline=setTimeout(()=>{console.error('Network integration smoke timed out');app.exit(1);},90000);deadline.unref();
const walk=w=>!w?[]:[w,...(w.children||[]).flatMap(walk),...walk(w.content)];
app.whenReady().then(async()=>{try{
 let win;for(let n=0;n<100;n++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}const js=code=>win.webContents.executeJavaScript(code);await js('window.studio.snapshot()');
 await js("document.getElementById('network-mode').click()");for(let n=0;n<100;n++){if(await js("!!document.getElementById('network-panel')"))break;await delay(100);}assert(await js("!!document.getElementById('server-concurrency')"));
 const port=await new Promise(resolve=>{const probe=net.createServer();probe.listen(0,'127.0.0.1',()=>{const p=probe.address().port;probe.close(()=>resolve(p));});});
 await js(`window.studio.configureNetwork(${JSON.stringify({port,concurrency:2,roots:[sources]})})`);const status=await js('window.studio.startServer()');const base=status.urls[0];
 async function api(index,route,payload){const response=await fetch(base+route,{method:payload?'POST':'GET',headers:{Authorization:'Bearer '+tokens[index].token},body:payload?JSON.stringify(payload):undefined});const result=await response.json();assert(response.ok,JSON.stringify(result));return result;}
 const rpc=(i,method,args={})=>api(i,'/api/rpc',{method,args});
 async function stopWhenIdle(){
  for(let attempt=0;attempt<100;attempt++){
   try{await js('window.studio.stopServer()');return;}
   catch(error){if(!error.message.includes('원격 작업/대기 작업'))throw error;}
   await delay(100);
  }
  throw new Error('Server did not become idle: '+JSON.stringify((await js('window.studio.networkStatus()')).sessions.map(s=>[s.name,s.status])));
 }
 async function clickMessages(i,id){const cursor=(await api(i,'/api/events?after=0')).seq;let done=false;const request=rpc(i,'gui.action',{id,action:'click'}).finally(()=>{done=true;});const seen=new Set();for(let n=0;n<500&&!done;n++){for(const e of (await api(i,'/api/events?after='+cursor)).events){if(e.event==='dialog'&&e.dialog.kind==='message'&&!seen.has(e.dialog.id)){seen.add(e.dialog.id);await rpc(i,'dialog.reply',{id:e.dialog.id,value:1024});}}await delay(20);}return request;}
 await Promise.all(tokens.map((_,i)=>api(i,'/api/status')));
 const ivsSource=path.join(sources,'ivs-rate.json');fs.writeFileSync(ivsSource,JSON.stringify({schema:'dmt-studio-ivs-v1',model:'TEST',lot_id:'TEST-',population:{strip_ids:['TEST-01','TEST-02','TEST-03'],strip_count:3,units_per_strip:64,nas_grid_units:[64],missing_barcode_folders:0,parse_errors:0,scope:'lot',complete:true,total_units:192},records:[{strip_id:'TEST-01',machine:'A',model:'TEST',lot:'TEST-',trial:'T',strip_folder:'1',container:'Unit',face:'T',unit_idx:'1',img_merge_idx:'0',ivs_1:'SM03',code_main:'SM03',code_sub:'',def_side:'F',strip_pt_x:1,strip_pt_y:1,img_pt_x:0,img_pt_y:0,source_xml:'test.xml',images:[]}]}));
 const ivsState=await rpc(2,'gui.snapshot');const openJson=walk(ivsState.root).find(w=>w.type==='button'&&w.text==='JSON 열기');const loading=rpc(2,'gui.action',{id:openJson.id,action:'click'});let inputDialog;
 for(let n=0;n<100;n++){const response=await api(2,'/api/events?after=0');inputDialog=response.events.find(e=>e.event==='dialog'&&e.dialog.kind==='file')?.dialog;if(inputDialog)break;await delay(30);}assert(inputDialog);await rpc(2,'dialog.reply',{id:inputDialog.id,value:ivsSource});await loading;const rateState=await rpc(2,'gui.snapshot');assert(walk(rateState.root).some(w=>w.type==='table'&&w.headers.length===4&&w.rows.some(row=>row.includes('0.520833%'))));assert(walk(rateState.root).some(w=>w.type==='button'&&w.text==='중지'&&!w.enabled));
 // All 65 defect records remain counted, while only 10 image sets per code are listed.
 const samples=path.join(sources,'image-samples.json'),samplePayload=JSON.parse(fs.readFileSync(ivsSource));const baseRecord=samplePayload.records[0];
 samplePayload.records=['SM03','OT12'].flatMap((code,index)=>Array.from({length:index?35:30},(_,i)=>({...baseRecord,unit_idx:String(i),ivs_1:code,code_main:code,images:[]})));fs.writeFileSync(samples,JSON.stringify(samplePayload));
 const beforeSamples=await api(2,'/api/events?after=0');const sampleLoading=rpc(2,'gui.action',{id:openJson.id,action:'click'});let sampleInput;
 for(let n=0;n<100;n++){const response=await api(2,'/api/events?after='+beforeSamples.seq);sampleInput=response.events.find(e=>e.event==='dialog'&&e.dialog.kind==='file')?.dialog;if(sampleInput)break;await delay(30);}assert(sampleInput);await rpc(2,'dialog.reply',{id:sampleInput.id,value:samples});await sampleLoading;
 let sampleState=await rpc(2,'gui.snapshot');assert(walk(sampleState.root).some(w=>w.type==='list'&&w.total===20));assert(walk(sampleState.root).some(w=>w.type==='table'&&w.rows.some(r=>r[0]==='SM03'&&r[1]==='30')));
 const more=walk(sampleState.root).find(w=>w.type==='button'&&w.text==='추가로 보기');await rpc(2,'gui.action',{id:more.id,action:'click'});sampleState=await rpc(2,'gui.snapshot');const rangeDialog=sampleState.dialogs.find(d=>d.title==='이미지 추가 보기 범위');assert(rangeDialog);
 const rangeWidgets=walk(rangeDialog.content),rangeCode=rangeWidgets.find(w=>w.type==='select'),rangeInputs=rangeWidgets.filter(w=>w.type==='input');await rpc(2,'gui.action',{id:rangeCode.id,action:'select',value:rangeCode.items.indexOf('SM03')});
 await rpc(2,'gui.action',{id:rangeInputs[0].id,action:'input',value:'11'});await rpc(2,'gui.action',{id:rangeInputs[1].id,action:'input',value:'20'});
 const rangeBrowser=new BrowserWindow({show:false,width:1500,height:1100,webPreferences:{partition:'dmt-network-range',contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:true,backgroundThrottling:false}});await rangeBrowser.loadURL(base);await rangeBrowser.webContents.executeJavaScript(`document.querySelector('.overlay input[type=password]').value=${JSON.stringify(tokens[2].token)};document.querySelector('.overlay form').requestSubmit()`);
 for(let n=0;n<100;n++){if(await rangeBrowser.webContents.executeJavaScript("!!document.querySelector('#modals input')"))break;await delay(50);}await rangeBrowser.webContents.executeJavaScript("lang='zh-TW';labels();paint(true)");
 assert(await rangeBrowser.webContents.executeJavaScript("document.querySelector('#modals').textContent.includes('影像追加檢視範圍')"));await delay(200);fs.writeFileSync(path.join(root,'image-range-zh.png'),(await rangeBrowser.webContents.capturePage()).toPNG());rangeBrowser.destroy();
 await rpc(2,'gui.action',{id:rangeWidgets.find(w=>w.type==='button'&&w.text==='추가로 보기').id,action:'click'});sampleState=await rpc(2,'gui.snapshot');assert(walk(sampleState.root).some(w=>w.type==='list'&&w.total===20));assert.equal(sampleState.dialogs.length,0);
 for(let i=0;i<2;i++){let s=await rpc(i,'gui.snapshot');await rpc(i,'gui.action',{id:s.root.id,action:'tab',value:6});s=await rpc(i,'gui.snapshot');const inputs=walk(s.root).filter(w=>w.type==='input');assert.equal(inputs.length,11);const values=['TEST',i===0?auditTxt:txt,'','',i===0?auditJson:'','',ids,'',i===0?auditSki:'',String(i===0?4:8),'3'];for(let n=0;n<inputs.length;n++)await rpc(i,'gui.action',{id:inputs[n].id,action:'input',value:values[n]});s=await rpc(i,'gui.snapshot');const calc=walk(s.root).find(w=>w.type==='button'&&w.text==='수율 계산');await clickMessages(i,calc.id);}
 const first=await rpc(0,'gui.snapshot'),second=await rpc(1,'gui.snapshot');assert(walk(first.root).some(w=>w.type==='text'&&w.text.includes('4')));assert(walk(second.root).some(w=>w.type==='text'&&w.text.includes('8')));
 // Export through the original save dialog and then download the A-side output.
 const exportButton=walk(first.root).find(w=>w.type==='button'&&w.text==='결과 JSON 내보내기');const exporting=rpc(0,'gui.action',{id:exportButton.id,action:'click'});let dialog;
 for(let n=0;n<100;n++){const e=await api(0,'/api/events?after=0');dialog=e.events.find(e=>e.event==='dialog'&&e.dialog.kind==='save')?.dialog;if(dialog)break;await delay(50);}assert(dialog);await rpc(0,'dialog.reply',{id:dialog.id,value:'remote-yield.json'});
 let saved;for(let n=0;n<100;n++){const e=await api(0,'/api/events?after=0');saved=e.events.find(e=>e.event==='dialog'&&e.dialog.kind==='message'&&e.dialog.id>dialog.id)?.dialog;if(saved)break;await delay(50);}assert(saved);await rpc(0,'dialog.reply',{id:saved.id,value:1024});await exporting;
 const files=await api(0,'/api/files');assert(files.some(f=>f.name==='remote-yield.json'));const download=await fetch(base+'/api/download?name=remote-yield.json',{headers:{Authorization:'Bearer '+tokens[0].token}});const summary=await download.json();assert.equal(summary.total_units,4);assert.equal((await api(1,'/api/files')).length,0);
 const archive=await api(0,'/api/archive',{});const zip=await(await fetch(base+'/api/download?name='+encodeURIComponent(archive.name),{headers:{Authorization:'Bearer '+tokens[0].token}})).arrayBuffer();assert.equal(Buffer.from(zip).readUInt16LE(),0x4b50);
 // Seed a finished A-PC verification without connecting the test to a NAS.
 const {DatabaseSync}=require('node:sqlite');
 const historyDir=path.join(root,'xml-followup');fs.mkdirSync(historyDir,{recursive:true});
 const historyDb=new DatabaseSync(path.join(historyDir,'history.sqlite'));
 historyDb.exec('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, model TEXT, lot TEXT, created REAL, updated REAL, status TEXT, baseline TEXT, details TEXT, xml TEXT)');
 const historyTime=Date.now()/1000;
 historyDb.prepare('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)').run('fixture','A-MODEL','RX-HISTORY',historyTime,historyTime,'difference',JSON.stringify({strips:['RX-HISTORY-01'],total_units:8}),JSON.stringify({xml_only:['RX-HISTORY-02']}),'');
 historyDb.close();
 for(const index of [0,1]){
  const state=await rpc(index,'gui.snapshot');
  await rpc(index,'gui.action',{id:state.root.id,action:'tab',value:11});
  const history=await rpc(index,'gui.snapshot');
  assert(walk(history.root).some(w=>w.type==='table'&&w.rows.some(row=>row.includes('RX-HISTORY')&&row.includes('차이 발견'))));
  assert(walk(history.root).some(w=>w.type==='text'&&w.text.includes('RX-HISTORY-02')));
  await rpc(index,'gui.action',{id:state.root.id,action:'tab',value:6});
 }
 const browser=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:true,backgroundThrottling:false}});await browser.loadURL(base);await browser.webContents.executeJavaScript(`document.querySelector('.overlay input[type=password]').value=${JSON.stringify(tokens[0].token)};document.querySelector('.overlay form').requestSubmit()`);
 for(let n=0;n<150;n++){if(await browser.webContents.executeJavaScript("document.querySelectorAll('#navigation button').length===12"))break;await delay(100);}assert(await browser.webContents.executeJavaScript("document.querySelectorAll('#navigation button').length===12"));
 assert(await browser.webContents.executeJavaScript("localStorage.setItem('language','zh-TW');lang='zh-TW';labels();paint(true);document.querySelector('#navigation').textContent.includes('IVS 分析/查詢')"));
 for(let n=0;n<100;n++){if(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('details.group button')).some(b=>b.textContent.includes('remote-yield.json'))"))break;await delay(100);}
 const destination=path.join(root,'browser-download.json');const downloaded=new Promise(resolve=>browser.webContents.session.once('will-download',(_e,item)=>{item.setSavePath(destination);item.once('done',(_e,state)=>resolve(state));}));await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('details.group button')).find(b=>b.textContent.includes('remote-yield.json')).click()");assert.equal(await downloaded,'completed');assert.equal(JSON.parse(fs.readFileSync(destination)).total_units,4);
 // B's actual browser opens the preview, then resolves original files on A.
 await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('#content button')).find(b=>b.textContent==='代碼預覽').click()");
 for(let n=0;n<100;n++){if(await browser.webContents.executeJavaScript("!!document.querySelector('.report-code-preview table')"))break;await delay(100);}
 const auditState=await rpc(0,'gui.snapshot');const auditDialog=auditState.dialogs.find(d=>d.title==='성적서 코드 미리보기');assert(auditDialog);const codeTable=walk(auditDialog.content).find(w=>w.type==='table'&&w.headers[0]==='성적서 코드');assert.deepEqual(codeTable.rows.map(r=>r.slice(0,2)),[['UNKNOWN','1'],['OT06','1']]);
 await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.report-code-tables table:first-child tbody tr')).find(r=>r.firstChild.textContent==='OT06').firstChild.click()");
 for(let n=0;n<100;n++){if(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.report-code-sources pre')).some(p=>p.textContent.includes('ivs_1=\"1111\"'))"))break;await delay(100);}
 assert(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.report-code-sources pre')).some(p=>p.textContent.includes('ivs_1=\"1111\"')&&p.textContent.includes('단일 불량률'))"));
 assert(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.report-code-sources pre')).some(p=>p.textContent.includes('▶ L1: TEST-01,1,1'))"));
 assert(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.report-code-sources select')).some(p=>p.textContent.includes('수율 계산.xml'))"));
 assert(await browser.webContents.executeJavaScript("document.querySelector('.report-code-preview').textContent.includes('計算當時的分配依據')"));browser.setSize(1640,1240);await delay(200);fs.writeFileSync(path.join(root,'report-code-preview-zh.png'),(await browser.webContents.capturePage()).toPNG());
 {
  let state=await rpc(0,'gui.snapshot'),widgets=walk(state.dialogs.find(d=>d.id===auditDialog.id).content);
  const codes=widgets.find(w=>w.type==='table'&&w.headers[0]==='성적서 코드');
  await rpc(0,'gui.action',{id:codes.id,action:'table',row:codes.rows.findIndex(r=>r[0]==='UNKNOWN'),column:0});
  const target=widgets.find(w=>w.type==='input'&&w.placeholder.includes('SM03')),note=widgets.find(w=>w.type==='input'&&w.placeholder.includes('필수'));
  await rpc(0,'gui.action',{id:target.id,action:'input',value:'SM03'});await rpc(0,'gui.action',{id:note.id,action:'input',value:'Reviewed original source'});
  state=await rpc(0,'gui.snapshot');
  widgets=walk(state.dialogs.find(d=>d.id===auditDialog.id).content);
  const units=widgets.find(w=>w.type==='table'&&w.headers[0]==='StripID');
  const pending=units.rows.findIndex(row=>row[1]==='2');
  assert(pending>=0);
  await rpc(0,'gui.action',{id:units.id,action:'table',row:pending,column:0});
  await rpc(0,'gui.action',{id:widgets.find(w=>w.type==='button'&&w.text==='선택 좌표 분류').id,action:'click'});
 }
 const reviewed=await rpc(0,'gui.snapshot'),reviewedCodes=walk(reviewed.dialogs.find(d=>d.id===auditDialog.id).content).find(w=>w.type==='table'&&w.headers[0]==='성적서 코드');assert.deepEqual(reviewedCodes.rows.map(r=>r.slice(0,2)),[['OT06','1'],['SM03','1']]);
 for(let n=0;n<100;n++){if(await browser.webContents.executeJavaScript("document.querySelector('.report-review')?.textContent.includes('未分類代碼檢查及分類')"))break;await delay(50);}
 assert(await browser.webContents.executeJavaScript("document.querySelector('.report-review').textContent.includes('分類所選座標')"));
 await browser.webContents.executeJavaScript("(async()=>{snapshot=await window.studio.snapshot();paint(true);})()");
 assert(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.report-code-tables table:first-child tbody tr')).some(r=>r.cells[0].textContent==='SM03'&&r.cells[1].textContent==='1')"));await delay(250);
 fs.writeFileSync(path.join(root,'report-review-zh.png'),(await browser.webContents.capturePage()).toPNG());
 await rpc(0,'gui.action',{id:auditDialog.id,action:'reject'});
 const yieldButtons=walk((await rpc(0,'gui.snapshot')).root).filter(w=>w.type==='button');
 const ot14Button=yieldButtons.find(w=>w.text==='OT14 좌표·RPT 원본 보기');
 await rpc(0,'gui.action',{id:ot14Button.id,action:'click'});
 const ot14Dialog=(await rpc(0,'gui.snapshot')).dialogs.find(d=>d.title==='OT14 좌표·RPT 원본 보기');
 assert(ot14Dialog);
 assert(walk(ot14Dialog.content).some(w=>w.type==='table'&&w.headers.includes('OT14 반영')));
 await rpc(0,'gui.action',{id:ot14Dialog.id,action:'reject'});
 const csvButton=yieldButtons.find(w=>w.text==='OT14 좌표·RPT 원본 CSV');
 const csvExport=rpc(0,'gui.action',{id:csvButton.id,action:'click'});
 let csvSave;
 for(let n=0;n<100;n++){
  csvSave=(await api(0,'/api/events')).events.find(e=>e.event==='dialog'&&e.dialog.kind==='save')?.dialog;
  if(csvSave)break;
  await delay(50);
 }
 assert(csvSave);
 await rpc(0,'dialog.reply',{id:csvSave.id,value:'OT14_audit.csv'});
 for(let n=0;n<100;n++){
  const saved=(await api(0,'/api/events')).events.find(e=>e.event==='dialog'&&e.dialog.kind==='message'&&e.dialog.id>csvSave.id)?.dialog;
  if(saved){await rpc(0,'dialog.reply',{id:saved.id,value:1024});break;}
  await delay(50);
 }
 await csvExport;
 for(let n=0;n<100;n++){
  if(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('details button')).some(b=>b.textContent==='直接檢視 CSV')"))break;
  await delay(80);
 }
 await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('details button')).find(b=>b.textContent==='直接檢視 CSV').click()");
 for(let n=0;n<100;n++){
  if(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.modal pre')).some(p=>p.textContent.includes('rpt_occurrences'))"))break;
  await delay(50);
 }
 assert(await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.modal pre')).some(p=>p.textContent.includes('rpt_occurrences'))"));
 await browser.webContents.executeJavaScript("Array.from(document.querySelectorAll('.modal')).find(m=>m.querySelector('h2')?.textContent==='OT14_audit.csv').querySelector('.modal-head button').click()");
 assert.equal(await browser.webContents.executeJavaScript("localStorage.getItem('dmt-access-key')!==null"),true);
 // Compare unmatched process TXT against SKI and navigate in the actual browser.
 const compareTxt=path.join(sources,'compare.txt'), compareSki=path.join(sources,'compare.ski');
 fs.writeFileSync(compareTxt,'1,TEST-,TEST-01\nTEST-,TEST-01,1,1,T,SM03\nTEST-,TEST-01,EOS\n2,TEST-,TEST-02\nTEST-,TEST-02,3,1,T,OT07\nTEST-,TEST-02,EOS\n');
 fs.writeFileSync(compareSki,'TEST-01,2,1\nTEST-02,1,1\n');
 const yieldState=walk((await rpc(0,'gui.snapshot')).root);
 const fields=yieldState.filter(widget=>widget.type==='input');
 for(const [index,value] of [[1,compareTxt],[4,''],[6,''],[8,compareSki]]){
  await rpc(0,'gui.action',{id:fields[index].id,action:'input',value});
 }
 const compareBrowser=code=>browser.webContents.executeJavaScript(code);
 await compareBrowser("dialogRequest=null;renderDialogs()");
 let calculationDone=false;
 const recalculation=rpc(0,'gui.action',{id:yieldState.find(w=>w.type==='button'&&w.text==='수율 계산').id,action:'click'})
  .finally(()=>{calculationDone=true;});
 for(let attempt=0;attempt<150&&!calculationDone;attempt++){
  await compareBrowser("if(dialogRequest?.kind==='message')document.querySelector('#modals .modal-actions button')?.click()");
  await delay(40);
 }
 assert(calculationDone,'Comparison calculation did not finish');
 await recalculation;
 await compareBrowser("(async()=>{snapshot=await window.studio.snapshot();paint(true);})()");
 assert(await compareBrowser("(()=>{const cell=Array.from(document.querySelectorAll('#content td')).find(c=>c.textContent==='TEST-01');if(!cell)return false;cell.click();return true;})()"));
 async function compareVisible(line){
  for(let attempt=0;attempt<100;attempt++){
   if(await compareBrowser(`document.querySelector('.its-coordinate-compare')?.textContent.includes(${JSON.stringify(line)})`))return;
   await delay(40);
  }
  throw new Error('Coordinate comparison did not display '+line);
 }
 await compareVisible('TEST-,TEST-01,1,1,T,SM03');
 await compareVisible('僅存在於 SKI 的座標');
 await compareBrowser("document.dispatchEvent(new KeyboardEvent('keydown',{key:'d',bubbles:true}))");
 await compareVisible('TEST-,TEST-02,3,1,T,OT07');
 await compareBrowser("document.dispatchEvent(new KeyboardEvent('keydown',{key:'A',bubbles:true}))");
 await compareVisible('TEST-,TEST-01,1,1,T,SM03');
 await compareBrowser("document.querySelector('.its-compare-next').click()");
 await compareVisible('TEST-,TEST-02,3,1,T,OT07');
 const disabledNext=await compareBrowser("document.querySelector('.its-compare-next[disabled]')?.dataset.control");
 assert(disabledNext);
 assert.deepEqual(await rpc(0,'gui.action',{id:disabledNext,action:'click'}),{applied:false,reason:'control-disabled'});
 await compareBrowser(`action(${JSON.stringify(disabledNext)},'click')`);
 assert(!await compareBrowser("document.getElementById('alert').textContent.includes('Control is disabled')"));
 await compareBrowser("document.querySelector('.its-coordinate-compare').closest('.modal').querySelector('.modal-head button').click()");
 // An already open B browser should recover after A restarts with the same
 // port and key. A reload clears stale widget IDs and old dialog events.
 let navigationCount=0;
 browser.webContents.on('did-finish-load',()=>navigationCount++);
 for(let n=0;n<100;n++){
  const state=await js('window.studio.networkStatus()');
  if(!state.active&&!state.queued)break;
  await delay(100);
 }
 await stopWhenIdle();
 console.log('A server stopped while B remained open');
 await js('window.studio.startServer()');
 console.log('A server restarted on original port');
 for(let n=0;n<150;n++){
  if(navigationCount&&!browser.webContents.isLoading()){
   const ready=await Promise.race([
    browser.webContents.executeJavaScript("document.querySelectorAll('#navigation button').length===12").catch(()=>false),
    delay(500).then(()=>false)
   ]);
   if(ready)break;
  }
  await delay(100);
 }
 assert(navigationCount>0);
 assert(await browser.webContents.executeJavaScript("document.querySelectorAll('#navigation button').length===12"));
 assert.equal((await api(0,'/api/download?name=remote-yield.json')).total_units,4);
 console.log('B browser reconnected and retained its result');
 browser.destroy();
 // Reopen all three workspaces before checking the independent processes.
 await Promise.all(tokens.map((_token,index)=>rpc(index,'gui.snapshot')));
 let report;
 for(let n=0;n<100;n++){
  report=await js('window.studio.networkStatus()');
  if(report.sessions.length===4&&new Set(report.measurement.latest.processes.map(p=>p.pid)).size===4)break;
  await delay(100);
 }
 assert.equal(report.sessions.length,4);
 assert(report.measurement.latest.backendRssBytes>0);
 assert.equal(new Set(report.measurement.latest.processes.map(p=>p.pid)).size,4);
 fs.writeFileSync(path.join(root,'network-panel.png'),(await win.webContents.capturePage()).toPNG());
 // The installed client window can open while the local server continues serving.
 await js(`window.studio.connectClient(${JSON.stringify(report.urls[0])})`);
 const localClient=BrowserWindow.getAllWindows().find(w=>w.id!==win.id);
 assert(localClient);
 assert(await localClient.webContents.executeJavaScript('window.studio.remote===true'));
 assert.equal((await js('window.studio.networkStatus()')).running,true);
 assert.equal((await js('window.studio.networkStatus()')).sessions.length,4);
 localClient.close();
 await stopWhenIdle();
 // Exercise the real client-mode IPC against a separate gateway (as on B PC).
 const {NetworkServer}=require('./network-server'),{Measurement}=require('./measurement'),{GuiClient}=require('./gui-client');const remoteRoot=path.join(root,'external-server');fs.mkdirSync(remoteRoot);const meter=new Measurement(remoteRoot);const external=new NetworkServer({root:remoteRoot,uiDir:path.join(__dirname,'ui'),createClient:(fn,opts)=>new GuiClient(fn,opts),seed:async()=>({}),local:{busy:false,pending:new Map()},measurement:meter,version:'test'});external.config.host='127.0.0.1';external.config.port=0;external.config.clients=tokens;await external.start();
 await js(`window.studio.connectClient(${JSON.stringify(external.addresses()[0])})`);const client=BrowserWindow.getAllWindows().find(w=>w.id!==win.id);assert(client);assert(await client.webContents.executeJavaScript("typeof require==='undefined'&&typeof process==='undefined'&&window.studio.remote===true"));
 const nativeDialog=require('electron').dialog;const originalMessage=nativeDialog.showMessageBox;nativeDialog.showMessageBox=async()=>({response:1});client.webContents.forcefullyCrashRenderer();
 for(let n=0;n<100;n++){if(fs.readFileSync(path.join(root,'diagnostics/events.jsonl'),'utf8').includes('renderer-gone'))break;await delay(50);}assert(fs.readFileSync(path.join(root,'diagnostics/events.jsonl'),'utf8').includes('renderer-gone'));assert(!win.isDestroyed());nativeDialog.showMessageBox=originalMessage;
 const originalSave=nativeDialog.showSaveDialog;const diagnosticFile=path.join(root,'diagnostic-export.json');nativeDialog.showSaveDialog=async()=>({canceled:false,filePath:diagnosticFile});await js('window.studio.exportDiagnostics()');nativeDialog.showSaveDialog=originalSave;const diagnostic=JSON.parse(fs.readFileSync(diagnosticFile));assert(diagnostic.logs['events.jsonl'].includes('renderer-gone'));assert(!JSON.stringify(diagnostic).includes(tokens[0].token));
 client.close();external.close();meter.close();
 console.log(JSON.stringify({threeIndependentBackends:true,originalYieldCalculation:true,isolatedResults:true,remoteSaveDownload:true,browserTenTabs:true,traditionalChinese:true,hostAndProcessMetrics:true,electronClientSandbox:true,rendererCrashCaptured:true,diagnosticExport:true,ivsSingleDefectRate:true,ivsStopControl:true,reportCodePreview:true,originalXmlSkiTxtComparison:true,rawSourcePreservedInChinese:true,tenSamplesPerCode:true,remoteImageRangeDialog:true,fullCountsPreserved:true}));app.quit();
}catch(e){console.error(e);app.exit(1);}});
