const electron=require('electron');const {app,BrowserWindow}=electron;
const path=require('node:path');const assert=require('node:assert/strict');const {spawn}=require('node:child_process');
app.setPath('userData',path.join(__dirname,'smoke-user-data','tray'));process.env.DMT_HEADLESS='1';app.disableHardwareAcceleration();
let tray,menu;
const moduleLifecycle=require('./lifecycle');const original=moduleLifecycle.installLifecycle;
moduleLifecycle.installLifecycle=options=>original({...options,electron:{...electron,
 Tray:function(image){tray=new electron.Tray(image);return tray;},
 Menu:{buildFromTemplate:template=>{menu=electron.Menu.buildFromTemplate(template);return menu;}}
}});
require('./desktop');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
if(app.hasSingleInstanceLock())app.whenReady().then(async()=>{
 try{
  let win;for(let i=0;i<100;i++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}
  const initial=await win.webContents.executeJavaScript('window.studio.snapshot()');assert.equal(initial.root.labels.length,10);
  assert(tray&&!tray.isDestroyed());win.close();await delay(150);assert(!win.isDestroyed()&&!win.isVisible());
  const child=spawn(process.execPath,[__filename,'--secondary'],{windowsHide:true,stdio:'ignore',env:process.env});
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{child.kill();reject(new Error('Duplicate instance did not exit'));},10000);child.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error('Duplicate failed: '+code));});child.once('error',reject);});
  assert(win.isVisible());assert.equal(BrowserWindow.getAllWindows().length,1);
  const after=await win.webContents.executeJavaScript('window.studio.snapshot()');assert.equal(after.root.id,initial.root.id);
  win.close();tray.emit('double-click');assert(win.isVisible());
  await win.webContents.executeJavaScript("window.studio.language('zh-TW')");assert.equal(menu.items[0].label,'開啟視窗');assert.equal(menu.items[2].label,'完全結束');
  await win.webContents.executeJavaScript("window.studio.language('ko')");assert.equal(menu.items[2].label,'완전히 종료');
  app.on('will-quit',()=>{assert(tray.isDestroyed());console.log(JSON.stringify({closeToTray:true,restore:true,duplicateProcessExited:true,sameBackend:true,localizedMenu:true,explicitQuit:true}));});
  win.close();menu.items[2].click();
 }catch(e){console.error(e);app.exit(1);}
});
