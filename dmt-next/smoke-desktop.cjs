const {app,BrowserWindow}=require('electron');
const path=require('node:path');const fs=require('node:fs');
app.setPath('userData',path.join(__dirname,'smoke-user-data'));
process.env.DMT_HEADLESS='1';
app.disableHardwareAcceleration();
require('./desktop');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
 try{
  let win;for(let i=0;i<100;i++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}
  await delay(1000);
  const initial=await win.webContents.executeJavaScript('window.studio.snapshot()');
  if(initial.root.labels.length!==12)throw new Error('Missing tabs');
  for(let i=0;i<11;i++){
   await win.webContents.executeJavaScript(`window.studio.action({id:${JSON.stringify(initial.root.id)},action:'tab',value:${i}})`);
   await delay(100);
   const count=await win.webContents.executeJavaScript("document.querySelectorAll('#content button').length");
   if(count===0)throw new Error('No controls in tab '+i);
  }
  await win.webContents.executeJavaScript("document.getElementById('language').value='zh-TW';document.getElementById('language').dispatchEvent(new Event('change'))");
  const translated=await win.webContents.executeJavaScript("document.getElementById('title').textContent");
  if(translated!=='作業員檢驗履歷')throw new Error('History tab translation failed: '+translated);
  await win.webContents.executeJavaScript(`window.studio.action({id:${JSON.stringify(initial.root.id)},action:'tab',value:11})`);
  await delay(150);
  const xmlHistory=await win.webContents.executeJavaScript("document.getElementById('title').textContent");
  if(xmlHistory!=='後續 XML 驗證履歷')throw new Error('XML history tab translation failed: '+xmlHistory);
  const originalPath='C:\\검사 통계\\수율 계산.xml';
  const literalTable={type:'table',id:'literal-history-test',headers:['작업자','원본 경로'],rows:[['테스트',originalPath]],start:0,total:1,selected:0,literalCells:true};
  const cells=await win.webContents.executeJavaScript(`Array.from(render(${JSON.stringify(literalTable)}).querySelectorAll('tbody td'),td=>td.textContent)`);
  if(cells[0]!=='테스트'||cells[1]!==originalPath)throw new Error('History original values were translated');
  const proof=await win.webContents.executeJavaScript(`render(${JSON.stringify({type:'text',readonly:true,literal:true,text:'원본 경로: '+originalPath+'\n88,RX-,RX-01\nRX-,RX-01,EOS'})}).textContent`);
  if(!proof.includes(originalPath)||!proof.includes('RX-,RX-01,EOS'))throw new Error('History original proof was translated');
  await delay(200);
  fs.writeFileSync(path.join(__dirname,'smoke-user-data','history-zh.png'),(await win.webContents.capturePage()).toPNG());
  await win.webContents.executeJavaScript("document.getElementById('language').value='ko';document.getElementById('language').dispatchEvent(new Event('change'))");
  await win.webContents.executeJavaScript(`window.studio.action({id:${JSON.stringify(initial.root.id)},action:'tab',value:8})`);
  await delay(100);
  await win.webContents.executeJavaScript("[...document.querySelectorAll('#content button')].find(b=>b.textContent==='미리보기').click()");
  await delay(250);
  const modal=await win.webContents.executeJavaScript("!!document.querySelector('#modals .modal')");
  if(!modal)throw new Error('Validation dialog not rendered');
  await win.webContents.executeJavaScript("document.querySelector('#modals .modal-actions button').click()");
  await delay(250);
  const state=await win.webContents.executeJavaScript('window.studio.snapshot()');if(state.busy)throw new Error('Dialog reply did not complete');
  await win.webContents.executeJavaScript("document.getElementById('workflow').click()");await delay(150);
  const workflowModal=await win.webContents.executeJavaScript("[...document.querySelectorAll('.modal h2')].some(e=>e.textContent.includes('LOT 테스트'))");
  if(!workflowModal)throw new Error('Workflow panel missing');
  const workflowImage=await win.webContents.capturePage();fs.writeFileSync(path.join(__dirname,'workflow-smoke.png'),workflowImage.toPNG());
  await win.webContents.executeJavaScript("document.querySelector('body > .overlay .modal-head button').click()");
  await win.webContents.executeJavaScript("window.studio.workflow('start',{model:'SMOKE',lot:'SMOKE01-'})");
  await win.webContents.executeJavaScript("[...document.querySelectorAll('#content button')].find(b=>b.textContent==='미리보기').click()");await delay(200);
  await win.webContents.executeJavaScript("document.querySelector('#modals .modal-actions button').click()");await delay(150);
  await win.webContents.executeJavaScript("window.studio.workflow('finish',{outcome:'failed',note:'Automated UI validation only'})");
  const exportPath=path.join(__dirname,'smoke-user-data','exported-workflows.json');
  require('electron').dialog.showSaveDialog=async()=>({canceled:false,filePath:exportPath});
  await win.webContents.executeJavaScript('window.studio.exportWorkflows()');
  const exported=JSON.parse(fs.readFileSync(exportPath,'utf8'));
  if(!exported.runs.some(r=>r.lot==='SMOKE01-'&&r.events.some(e=>e.event==='action')))throw new Error('Workflow action log not exported');
  await win.webContents.executeJavaScript(`window.studio.action({id:${JSON.stringify(initial.root.id)},action:'tab',value:0})`);
  await delay(800);
  try{const image=await win.webContents.capturePage();fs.writeFileSync(path.join(__dirname,'studio-smoke.png'),image.toPNG());}catch(e){console.log('Screenshot unavailable: '+e.message);}
  console.log(JSON.stringify({tabs:initial.root.labels.length,translation:translated,dialogRoundTrip:true,workflowPanel:true,workflowExport:true}));
  app.quit();
 }catch(e){console.error(e);app.exit(1);}
});
