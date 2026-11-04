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
  if(initial.root.labels.length!==10)throw new Error('Missing tabs');
  for(let i=0;i<10;i++){
   await win.webContents.executeJavaScript(`window.studio.action({id:${JSON.stringify(initial.root.id)},action:'tab',value:${i}})`);
   await delay(100);
   const count=await win.webContents.executeJavaScript("document.querySelectorAll('#content button').length");
   if(count===0)throw new Error('No controls in tab '+i);
  }
  await win.webContents.executeJavaScript("document.getElementById('language').value='zh-TW';document.getElementById('language').dispatchEvent(new Event('change'))");
  const translated=await win.webContents.executeJavaScript("document.getElementById('title').textContent");
  if(translated==='테스트')throw new Error('Translation failed');
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
