const {app,BrowserWindow}=require('electron');const path=require('node:path');
app.setPath('userData',path.join(__dirname,'smoke-user-data','recovery'));app.disableHardwareAcceleration();
process.env.DMT_HEADLESS='1';process.env.DMT_PYTHON=path.join(__dirname,'missing-test-backend.exe');
require('./desktop');const {autoUpdater}=require('electron-updater');let installs=0;autoUpdater.quitAndInstall=()=>installs++;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
 try{
  let win;for(let i=0;i<100;i++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}await delay(700);
  if(!await win.webContents.executeJavaScript("!!document.getElementById('backend-recovery')"))throw new Error('Missing recovery UI');
  autoUpdater.emit('update-not-available',{version:app.getVersion()});
  await win.webContents.executeJavaScript("document.getElementById('updates').click()");await delay(100);
  const message=await win.webContents.executeJavaScript("document.querySelector('#update-dialog').textContent");
  if(!message.includes('최신 버전'))throw new Error('No-update popup failed');
  if(installs||win.isDestroyed())throw new Error('No-update caused install/exit');
  await win.webContents.executeJavaScript("document.getElementById('update-later').click()");
  delete process.env.DMT_PYTHON;
  await win.webContents.executeJavaScript("[...document.querySelectorAll('#backend-recovery button')].find(b=>b.textContent==='다시 연결').click()");
  for(let i=0;i<80;i++){if(!await win.webContents.executeJavaScript("!!document.getElementById('backend-recovery')"))break;await delay(100);}
  const state=await win.webContents.executeJavaScript('window.studio.snapshot()');if(state.root.labels.length!==10)throw new Error('Backend recovery failed');
  console.log(JSON.stringify({backendFailureKeptAppOpen:true,noUpdateKeptAppOpen:true,installCalls:installs,recoveredTabs:10}));app.quit();
 }catch(e){console.error(e);app.exit(1);}
});
