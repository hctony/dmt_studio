const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const {EventEmitter}=require('node:events');const {PassThrough}=require('node:stream');
function setup(missing=false){
 const children=[],events=[];const module={exports:{}};
 const app={isPackaged:true,getPath:()=>'/test'};
 const fakeSpawn=()=>{const c=new EventEmitter();c.stdin=new PassThrough();c.stdout=new PassThrough();c.stderr=new PassThrough();c.kill=()=>c.emit('exit',0);children.push(c);return c;};
 vm.runInNewContext(fs.readFileSync(require.resolve('./gui-client'),'utf8'),{module,process:{resourcesPath:'/app/resources',env:{}},__dirname:__dirname,setTimeout,clearTimeout,
 require:name=>name==='electron'?{app}:name==='node:child_process'?{spawn:fakeSpawn}:name==='node:fs'?{existsSync:()=>!missing}:require(name)});
 return {client:new module.exports.GuiClient(e=>events.push(e)),children,events};
}
test('missing Qt runtime is reported without launching or terminating UI',async()=>{
 const s=setup(true);await assert.rejects(s.client.call('gui.snapshot'),/BACKEND_FILES_MISSING/);
 assert.equal(s.children.length,0);assert.equal(s.events[0].event,'backend-error');
});
test('startup failure is latched until explicit retry',async()=>{
 const s=setup();const first=s.client.call('gui.snapshot');s.children[0].stderr.write('shiboken6 missing');s.children[0].emit('exit',1);
 await assert.rejects(first,/shiboken6/);await assert.rejects(s.client.call('workflow.status'),/shiboken6/);assert.equal(s.children.length,1);
 const retried=s.client.retry();assert.equal(s.children.length,2);
 s.children[1].stdout.write(JSON.stringify({id:2,result:{ok:true}})+'\n');assert.equal((await retried).ok,true);s.client.close();
});
test('update shutdown blocks calls and waits for backend exit',async()=>{
 const s=setup();s.client.start();let stopped=false;const done=s.client.stopForUpdate().then(()=>stopped=true);
 await assert.rejects(s.client.call('gui.snapshot'),/UPDATE_INSTALLING/);assert.equal(stopped,false);
 s.children[0].emit('exit',0);await done;assert.equal(stopped,true);assert.equal(s.events.length,0);
});

test('backend exit records native code, last context, resource sample and stderr',async()=>{
 const s=setup(),written=[],stderr=[];s.client.options.diagnostics={write:(kind,data)=>{written.push({kind,data});return 'diagnostic-id';},stderr:(label,value)=>stderr.push(value)};
 const request=s.client.call('gui.snapshot');s.children[0].stdout.write(JSON.stringify({event:'snapshot',snapshot:{busy:true,context:{lot_in:'RX-'},tasks:[{detail:'last.xml'}]}})+'\n');s.children[0].stdout.write(JSON.stringify({event:'resources',resources:{rssBytes:123456}})+'\n');s.children[0].stderr.write('QThread: Destroyed while thread is still running');s.children[0].emit('exit',3221226505,null);await assert.rejects(request,/QThread/);
 const failure=written.find(e=>e.kind==='backend-failure').data;assert.equal(failure.exitHex,'0xc0000409');assert.equal(failure.context.lot_in,'RX-');assert.equal(failure.resources.rssBytes,123456);assert(stderr.join('').includes('QThread'));assert.equal(s.events.at(-1).diagnosticId,'diagnostic-id');
});
