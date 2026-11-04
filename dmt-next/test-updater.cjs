const test=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');
const {installUpdater}=require('./updater');
function setup(overrides={}){
 const updater=new EventEmitter();const app=new EventEmitter();app.isPackaged=false;app.getVersion=()=> '0.3.1';
 const calls=[],states=[],handlers={};let busy=false,locked=false;
 updater.quitAndInstall=(...args)=>calls.push(args);
 updater.checkForUpdates=async()=>{};
 installUpdater(app,{handle:(key,fn)=>handlers[key]=fn},()=>busy,{updater,onState:s=>states.push(s),beforeInstall:()=>locked=true,onInstallError:()=>locked=false,...overrides});
 return {app,updater,calls,states,handlers,setBusy:value=>busy=value,locked:()=>locked};
}
test('only a completed, idle update installs silently and force restarts',async()=>{
 const s=setup();await assert.rejects(s.handlers['update:install'],/UPDATE_NOT_READY/);
 s.updater.emit('update-downloaded',{version:'0.3.2'});s.setBusy(true);
 await assert.rejects(s.handlers['update:install'],/UPDATE_BUSY/);assert.equal(s.calls.length,0);
 s.setBusy(false);await s.handlers['update:install']();assert.deepEqual(s.calls,[[true,true]]);assert.equal(s.locked(),true);
 await assert.rejects(s.handlers['update:install'],/UPDATE_NOT_READY/);assert.equal(s.calls.length,1);
});
test('download and ready state are pushed to renderer',()=>{
 const s=setup();s.updater.emit('update-available',{version:'0.3.2'});s.updater.emit('download-progress',{percent:52.4});
 assert.equal(s.states.at(-1).progress,52);s.updater.emit('update-downloaded',{version:'0.3.2'});
 assert.equal(s.states.at(-1).status,'ready');assert.equal(s.states.at(-1).progress,100);
 assert.equal(s.updater.autoRunAppAfterInstall,true);
});
test('installer failure clears input lock and is recoverable',async()=>{
 const s=setup();s.updater.emit('update-downloaded',{version:'0.3.2'});await s.handlers['update:install']();
 s.updater.emit('error',new Error('Installer unavailable'));
 assert.equal(s.locked(),false);assert.equal(s.handlers['update:status']().status,'error');
});
test('download errors cannot install and busy state is live',async()=>{
 const s=setup();s.updater.emit('update-available',{version:'0.3.2'});s.updater.emit('error',new Error('Offline'));
 await assert.rejects(s.handlers['update:install'],/UPDATE_NOT_READY/);assert.equal(s.calls.length,0);
 s.setBusy(true);assert.equal(s.handlers['update:status']().busy,true);s.setBusy(false);assert.equal(s.handlers['update:status']().busy,false);
});
test('no update check never installs or quits, including cached same version',async()=>{
 const s=setup();s.app.isPackaged=true;
 s.updater.checkForUpdates=async()=>s.updater.emit('update-not-available',{version:'0.3.1'});
 for(let i=0;i<3;i++)assert.equal((await s.handlers['update:check']()).status,'current');
 s.updater.emit('update-downloaded',{version:'0.3.1'});
 await assert.rejects(s.handlers['update:install'],/UPDATE_NOT_READY/);
 assert.equal(s.calls.length,0);assert.equal(s.locked(),false);assert.equal(s.updater.autoInstallOnAppQuit,false);
});
test('installer waits until backend has fully stopped',async()=>{
 let finish;const s=setup({beforeInstall:()=>new Promise(r=>finish=r)});
 s.updater.emit('update-downloaded',{version:'0.3.2'});const pending=s.handlers['update:install']();
 assert.equal(s.calls.length,0);finish();await pending;assert.equal(s.calls.length,1);
});
test('failed backend shutdown blocks installation',async()=>{
 const s=setup({beforeInstall:async()=>{throw new Error('BACKEND_STOP_TIMEOUT');}});
 s.updater.emit('update-downloaded',{version:'0.3.2'});
 await assert.rejects(s.handlers['update:install'],/BACKEND_STOP_TIMEOUT/);assert.equal(s.calls.length,0);
});
