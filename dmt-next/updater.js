function installUpdater(app, ipcMain, isBusy, options = {}) {
  const autoUpdater = options.updater || require('electron-updater').autoUpdater;
  let state = { status: app.isPackaged ? 'idle' : 'development', version: app.getVersion() };
  const publish = patch => {state = {...state, ...patch}; options.onState?.({...state}); return state;};
  autoUpdater.autoDownload = true;
  autoUpdater.requestHeaders = {...autoUpdater.requestHeaders, 'Cache-Control':'no-cache'};
  // Installation is allowed only through the explicit update/restart action.
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.autoRunAppAfterInstall = true;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.on('checking-for-update', () => publish({status:'checking', progress:0, error:null, available:null}));
  autoUpdater.on('update-available', info => publish({status:'downloading', available:info.version, progress:0}));
  autoUpdater.on('update-not-available', () => publish({status:'current', progress:0, available:null}));
  autoUpdater.on('download-progress', info => publish({progress:Math.round(info.percent)}));
  autoUpdater.on('update-downloaded', info => publish(info.version===app.getVersion()
    ? {status:'current', available:null, progress:0}
    : {status:'ready', available:info.version, progress:100}));
  const failed = error => {options.onInstallError?.(); publish({status:'error', error:error.message});};
  autoUpdater.on('error', failed);
  const status = () => ({...state, busy:!!isBusy()});
  const check = async () => { if (app.isPackaged && !['checking','downloading','ready','installing'].includes(state.status)) { try { await autoUpdater.checkForUpdates(); } catch (error) { failed(error); } } return status(); };
  ipcMain.handle('update:status', status);
  ipcMain.handle('update:check', check);
  ipcMain.handle('update:install', async () => {
    if (state.status !== 'ready') throw new Error('UPDATE_NOT_READY');
    if (isBusy()) throw new Error('UPDATE_BUSY');
    publish({status:'installing'});
    try {
      await options.beforeInstall?.();
      if(state.status!=='installing')throw new Error('UPDATE_ABORTED');
      // NSIS /S + --force-run: install silently and reopen the updated app.
      autoUpdater.quitAndInstall(true, true);
    } catch (error) { failed(error); throw error; }
  });
  if (app.isPackaged) { check(); const timer = setInterval(check, 6 * 60 * 60 * 1000); timer.unref(); app.on('before-quit', () => clearInterval(timer)); }
}
module.exports = { installUpdater };
