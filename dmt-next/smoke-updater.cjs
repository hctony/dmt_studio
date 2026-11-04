const {app,BrowserWindow}=require('electron');const path=require('node:path');const fs=require('node:fs');
app.setPath('userData',path.join(__dirname,'smoke-user-data','updater'));
process.env.DMT_HEADLESS='1';app.disableHardwareAcceleration();
require('./desktop');
const {autoUpdater}=require('electron-updater');const delay=ms=>new Promise(r=>setTimeout(r,ms));
let installArgs=null;autoUpdater.quitAndInstall=(...args)=>{installArgs=args;};
app.whenReady().then(async()=>{
 try{
  let win;for(let i=0;i<100;i++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}
  await delay(800);await win.webContents.executeJavaScript('window.studio.snapshot()');
  await win.webContents.executeJavaScript("document.getElementById('language').value='zh-TW';document.getElementById('language').dispatchEvent(new Event('change'))");
  autoUpdater.emit('update-available',{version:'0.3.2'});autoUpdater.emit('download-progress',{percent:35});await delay(150);
  let state=await win.webContents.executeJavaScript("({text:document.querySelector('#update-restart')?.textContent,disabled:document.querySelector('#update-restart')?.disabled})");
  if(state.text!=='更新並重新啟動'||!state.disabled)throw new Error('Traditional Chinese download popup failed');
  autoUpdater.emit('update-downloaded',{version:'0.3.2'});await delay(150);
  state=await win.webContents.executeJavaScript("({text:document.querySelector('#update-restart').textContent,disabled:document.querySelector('#update-restart').disabled})");
  if(state.disabled)throw new Error('Ready button disabled');
  fs.writeFileSync(path.join(__dirname,'update-zh-smoke.png'),(await win.webContents.capturePage()).toPNG());
  await win.webContents.executeJavaScript("document.getElementById('language').value='ko';document.getElementById('language').dispatchEvent(new Event('change'))");
  if(await win.webContents.executeJavaScript("document.querySelector('#update-restart').textContent")!=='업데이트 및 재시작')throw new Error('Korean popup failed');
  await win.webContents.executeJavaScript("document.getElementById('update-later').click()");await delay(1100);
  if(await win.webContents.executeJavaScript("!!document.getElementById('update-dialog')"))throw new Error('Dismissed popup reopened');
  await win.webContents.executeJavaScript("document.getElementById('updates').click()");await delay(150);
  await win.webContents.executeJavaScript("document.getElementById('update-restart').click()");await delay(150);
  if(JSON.stringify(installArgs)!=='[true,true]')throw new Error('Silent install + restart was not requested');
  console.log(JSON.stringify({downloadPopup:true,zhTW:true,korean:true,later:true,reopen:true,silentInstallAndRestart:installArgs}));
  app.quit();
 }catch(error){console.error(error);app.exit(1);}
});
