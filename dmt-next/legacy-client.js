const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const path = require('node:path');
const { app } = require('electron');
class LegacyClient {
  constructor() { this.pending = new Map(); this.sequence = 0; this.child = null; }
  start() {
    if (this.child) return;
    const executable = app.isPackaged ? path.join(process.resourcesPath, 'backend', 'dmt-service.exe') : process.env.DMT_PYTHON || 'python';
    const args = app.isPackaged ? [] : ['-u', path.join(__dirname, 'legacy_bridge.py')];
    const child = spawn(executable, args, { cwd: app.getPath('userData'), env: {...process.env, DMT_DATA_DIR: app.getPath('userData')}, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    child.stdin.on('error', () => {});
    child.stderr.on('data', data => { this.lastError = data.toString().slice(-2000); });
    const fail = error => { for (const item of this.pending.values()) item.reject(error); this.pending.clear(); this.child = null; };
    child.on('error', fail);
    child.on('exit', () => fail(new Error(this.lastError || 'Backend stopped')));
    createInterface({ input: child.stdout }).on('line', line => {
      try { const message = JSON.parse(line); const item = this.pending.get(message.id); if (!item) return; this.pending.delete(message.id); message.error ? item.reject(new Error(message.error)) : item.resolve(message.result); }
      catch { fail(new Error('Invalid backend response')); child.kill(); }
    });
  }
  call(method, args = {}) { this.start(); const id = ++this.sequence; return new Promise((resolve, reject) => { this.pending.set(id, { resolve, reject }); this.child.stdin.write(JSON.stringify({id, method, args}) + '\n'); }); }
  close() { this.child?.kill(); }
}
module.exports = { LegacyClient };
