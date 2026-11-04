const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const { LegacyClient } = require('./legacy-client');
const legacy = new LegacyClient();
const { installUpdater } = require('./updater');

const backend = app.isPackaged ? path.join(process.resourcesPath, 'dmt-backend.exe') : path.join(__dirname, 'backend', process.platform === 'win32' ? 'dmt-backend.exe' : 'dmt-backend');
function call(command, input) {
  return new Promise((resolve, reject) => {
    const child = execFile(backend, [command], { windowsHide: true }, (error, stdout, stderr) => {
      if (error) reject(new Error(stderr || error.message));
      else resolve(stdout ? JSON.parse(stdout) : null);
    });
    child.stdin.end(input === undefined ? '' : JSON.stringify(input));
  });
}
app.whenReady().then(() => {
  let originalWindow;
  ipcMain.handle('legacy:open', () => {
    if (originalWindow) return;
    const executable = app.isPackaged ? path.join(process.resourcesPath,'backend','dmt-service.exe') : process.env.DMT_PYTHON || 'python';
    const args = app.isPackaged ? ['--legacy-ui'] : [path.join(__dirname,'legacy_bridge.py'),'--legacy-ui'];
    originalWindow = spawn(executable,args,{cwd:app.getPath('userData'),env:{...process.env,DMT_DATA_DIR:app.getPath('userData')},windowsHide:true,stdio:'ignore'});
    originalWindow.on('error', () => { originalWindow = null; });
    originalWindow.on('exit', () => { originalWindow = null; });
  });
  installUpdater(app, ipcMain, () => legacy.pending.size > 0 || !!originalWindow);
  ipcMain.handle('legacy:cache-status', () => legacy.call('cache.status'));
  ipcMain.handle('legacy:cache-clear', () => legacy.call('cache.clear'));
  ipcMain.handle('legacy:settings', () => legacy.call('settings.read'));
  const analysisMethods = new Set(['ivs.scan', 'strip.scan', 'barcode.scan', 'trial.list', 'strip.search', 'tests.run', 'yield.calculate', 'strip.validate', 'inspection.stats', 'lm.preview', 'barcode.compare', 'strip.missing', 'gather.parse', 'gather.files', 'bbt.load', 'ski.load', 'lot.infer']);
  ipcMain.handle('file:choose', async () => { const r = await dialog.showOpenDialog({properties:['openFile','multiSelections']}); return r.canceled ? [] : r.filePaths; });
  ipcMain.handle('result:save', async (_event, value) => { const r = await dialog.showSaveDialog({defaultPath:'result.json'}); if (r.canceled) return; await fs.writeFile(r.filePath, JSON.stringify(value,null,2),'utf8'); return r.filePath; });
  ipcMain.handle('legacy:analyze', (_event, method, args) => {
    if (!analysisMethods.has(method)) throw new Error('Unsupported analysis operation');
    return legacy.call(method, args);
  });
  ipcMain.handle('profiles:load', () => call('load'));
  ipcMain.handle('profiles:save', (_event, state) => call('save', state));
  ipcMain.handle('directory:choose', async () => {
    const result = await dialog.showOpenDialog({ properties: ['openDirectory'] });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle('model-settings:import', async () => {
    const result = await dialog.showOpenDialog({ properties: ['openFile'], filters: [{ name: 'JSON', extensions: ['json', 'txt'] }] });
    if (result.canceled) return null;
    const bytes = await fs.readFile(result.filePaths[0]);
    if (bytes.length > 5 * 1024 * 1024) throw new Error('설정 파일이 너무 큽니다.');
    const settings = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));
    if (!settings || Array.isArray(settings) || typeof settings !== 'object' || Object.values(settings).some(v => !v || Array.isArray(v) || typeof v !== 'object')) throw new Error('모델별 설정 JSON 형식이 아닙니다.');
    const dir = app.getPath('userData');
    await fs.mkdir(dir, { recursive: true });
    // Back up the imported original; do not reinterpret Taiwan-specific paths.
    const dest = path.join(dir, 'yield_model_settings.json');
    try { await fs.copyFile(dest, dest + '.bak'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    await fs.writeFile(dest, JSON.stringify(settings, null, 2), 'utf8');
    return { count: Object.keys(settings).length, path: dest };
  });
  const win = new BrowserWindow({ width: 900, height: 760, webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
  win.loadFile(path.join(__dirname, 'ui', 'index.html'));
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', () => legacy.close());
