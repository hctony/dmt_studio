const {app, BrowserWindow} = require('electron');
const path = require('node:path');
app.setPath('userData', path.join(__dirname,'smoke-user-data'));
require('./main');
app.whenReady().then(async () => {
  const win = BrowserWindow.getAllWindows()[0];
  try {
    if (win.webContents.isLoading()) await new Promise(resolve => win.webContents.once('did-finish-load',resolve));
    const result = await win.webContents.executeJavaScript(`(async()=>({title:document.title, screens:document.querySelectorAll('section select option').length, cache:await window.dmt.cacheStatus(), update:await window.dmt.updateStatus()}))()`);
    console.log(JSON.stringify(result));
    if(result.screens!==10 || result.cache.ski!==0) throw new Error('Smoke check failed');
    app.exit(0);
  } catch(e) { console.error(e); app.exit(1); }
});
