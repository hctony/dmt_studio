const test=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');
const {installUpdater}=require('./updater');
function setup(){
 const updater=new EventEmitter();const app=new EventEmitter();app.isPackaged=false;app.getVersion=()=> '0.3.1';
 const calls=[],states=[],handlers={};let busy=false,locked=false;
 updater.quitAndInstall=(...args)=>calls.push(args);
 updater.checkForUpdates=async()=>{};
 installUpdater(app,{handle:(key,fn)=>handlers[key]=fn},()=>busy,{updater,onState:s=>states.push(s),beforeInstall:()=>locked=true,onInstallError:()=>locked=false});
 return {updater,calls,states,handlers,setBusy:value=>busy=value,locked:()=>locked};
}
test('only a completed, idle update installs silently and force restarts',()=>{
 const s=setup();assert.throws(s.handlers['update:install'],/UPDATE_NOT_READY/);
 s.updater.emit('update-downloaded',{version:'0.3.2'});s.setBusy(true);
 assert.throws(s.handlers['update:install'],/UPDATE_BUSY/);assert.equal(s.calls.length,0);
 s.setBusy(false);s.handlers['update:install']();assert.deepEqual(s.calls,[[true,true]]);assert.equal(s.locked(),true);
 assert.throws(s.handlers['update:install'],/UPDATE_NOT_READY/);assert.equal(s.calls.length,1);
});
test('download and ready state are pushed to renderer',()=>{
 const s=setup();s.updater.emit('update-available',{version:'0.3.2'});s.updater.emit('download-progress',{percent:52.4});
 assert.equal(s.states.at(-1).progress,52);s.updater.emit('update-downloaded',{version:'0.3.2'});
 assert.equal(s.states.at(-1).status,'ready');assert.equal(s.states.at(-1).progress,100);
 assert.equal(s.updater.autoRunAppAfterInstall,true);
});
test('installer failure clears input lock and is recoverable',()=>{
 const s=setup();s.updater.emit('update-downloaded',{version:'0.3.2'});s.handlers['update:install']();
 s.updater.emit('error',new Error('Installer unavailable'));
 assert.equal(s.locked(),false);assert.equal(s.handlers['update:status']().status,'error');
});
test('download errors cannot install and busy state is live',()=>{
 const s=setup();s.updater.emit('update-available',{version:'0.3.2'});s.updater.emit('error',new Error('Offline'));
 assert.throws(s.handlers['update:install'],/UPDATE_NOT_READY/);assert.equal(s.calls.length,0);
 s.setBusy(true);assert.equal(s.handlers['update:status']().busy,true);s.setBusy(false);assert.equal(s.handlers['update:status']().busy,false);
});
