const {app,BrowserWindow,dialog}=require('electron');
const path=require('node:path');const fs=require('node:fs');const assert=require('node:assert/strict');
const root=path.join(__dirname,'smoke-user-data','model-import');fs.mkdirSync(root,{recursive:true});
app.setPath('userData',root);process.env.DMT_HEADLESS='1';app.disableHardwareAcceleration();
const fixture=path.join(root,'original-models.txt');
fs.writeFileSync(fixture,'\uFEFF'+JSON.stringify({
 'IMPORT-A':{units_per_strip:'64',xout_limit:'13',vrs_txt_paths:'Z:\\Taiwan\\LOT01\\VRS.txt',fvi_txt_paths:'',ivs_txt_paths:'',ivs_json_paths:'',skipdata_path:'Z:\\Taiwan\\Skipdata',ski_path:''},
 'IMPORT-B':{units_per_strip:88,xout_limit:18,vrs_txt_paths:'',fvi_txt_paths:'Z:\\Taiwan\\LOT02\\FVI.txt',ivs_txt_paths:'',ivs_json_paths:'',skipdata_path:'',ski_path:''}
}));
dialog.showOpenDialog=async()=>({canceled:false,filePaths:[fixture]});
require('./desktop');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
 try{
  let win;for(let i=0;i<100;i++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}
  const js=code=>win.webContents.executeJavaScript(code);
  await js('window.studio.snapshot()');
  await js("document.getElementById('language').value='ko';document.getElementById('language').dispatchEvent(new Event('change'));document.getElementById('import').click()");
  for(let i=0;i<50;i++){if(await js("!!document.getElementById('model-import')"))break;await delay(100);}
  await js("document.getElementById('model-import').click()");
  for(let i=0;i<50;i++){if(await js("document.getElementById('model-message').textContent.includes('2개 모델')"))break;await delay(100);}
  assert.match(await js("document.getElementById('model-message').textContent"),/2개 모델/);
  await js("document.getElementById('saved-model').value='IMPORT-B';document.getElementById('saved-model').dispatchEvent(new Event('change'));document.getElementById('model-gathering').checked=true;document.getElementById('model-apply').click()");
  for(let i=0;i<50;i++){if(await js("document.getElementById('model-message').textContent.includes('적용 완료')"))break;await delay(100);}
  assert.match(await js("document.getElementById('model-message').textContent"),/IMPORT-B.*Gathering/);
  await js('window.studio.retryBackend()');
  const inputs=JSON.parse(fs.readFileSync(path.join(root,'gui-inputs.json'),'utf8'));
  assert.equal(inputs['6/YieldTab:/xout_limit_in'],'18');assert.equal(inputs['6/YieldTab:/vrs_txt_in'],'');
  assert.equal(inputs['3/GatheringTab:/allowance_in'],'18');
  assert.equal((await js('window.studio.modelSettings()')).selected,'IMPORT-B');
  await js("document.querySelector('#model-settings').closest('.overlay').remove();document.getElementById('language').value='zh-TW';document.getElementById('language').dispatchEvent(new Event('change'));document.getElementById('import').click()");
  for(let i=0;i<50;i++){if(await js("!!document.getElementById('model-import')"))break;await delay(100);}
  assert.equal(await js("document.getElementById('model-import').textContent"),'匯入舊版 JSON');
  const image=await win.webContents.capturePage();fs.writeFileSync(path.join(__dirname,'model-settings-smoke.png'),image.toPNG());
  console.log(JSON.stringify({import:true,models:2,xout:18,restartPersistence:true,gatheringOptIn:true,traditionalChinese:true}));app.quit();
 }catch(e){console.error(e);app.exit(1);}
});
