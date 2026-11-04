const { autoUpdater } = require('electron-updater');
function installUpdater(app, ipcMain, isBusy) {
  let state = { status: app.isPackaged ? 'idle' : 'development', version: app.getVersion() };
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.on('checking-for-update', () => { state = {...state, status:'checking'}; });
  autoUpdater.on('update-available', info => { state = {...state, status:'downloading', available: info.version}; });
  autoUpdater.on('update-not-available', () => { state = {...state, status:'current'}; });
  autoUpdater.on('download-progress', info => { state = {...state, progress: Math.round(info.percent)}; });
  autoUpdater.on('update-downloaded', info => { state = {...state, status:'ready', available:info.version}; });
  autoUpdater.on('error', error => { state = {...state, status:'error', error:error.message}; });
  const check = async () => { if (app.isPackaged && !['checking','downloading','ready'].includes(state.status)) { try { await autoUpdater.checkForUpdates(); } catch {} } return state; };
  ipcMain.handle('update:status', () => state);
  ipcMain.handle('update:check', check);
  ipcMain.handle('update:install', () => { if (state.status !== 'ready') throw new Error('Update is not ready'); if (isBusy()) throw new Error('Analysis is running'); autoUpdater.quitAndInstall(); });
  if (app.isPackaged) { check(); const timer = setInterval(check, 6 * 60 * 60 * 1000); timer.unref(); app.on('before-quit', () => clearInterval(timer)); }
}
module.exports = { installUpdater };
