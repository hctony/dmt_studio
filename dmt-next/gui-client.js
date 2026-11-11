const {spawn} = require('node:child_process');
const {createInterface} = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const {app} = require('electron');
class GuiClient {
  constructor(onEvent, options={}) { this.onEvent=onEvent;this.options=options;this.pending=new Map();this.sequence=0;this.busy=false;this.snapshot=null;this.child=null;this.failure=null; }
  start() {
    if(this.child)return;
    const exe=app.isPackaged?path.join(process.resourcesPath,'backend','dmt-service.exe'):(process.env.DMT_PYTHON||'python');
    if(app.isPackaged){
      const root=path.dirname(exe);
      const required=['dmt-service.exe','_internal/shiboken6/Shiboken.pyd','_internal/shiboken6/shiboken6.abi3.dll','_internal/PySide6/QtCore.pyd','_internal/PySide6/QtGui.pyd','_internal/PySide6/QtWidgets.pyd'];
      const missing=required.filter(name=>!fs.existsSync(path.join(root,name)));
      if(missing.length){this.failure=new Error('BACKEND_FILES_MISSING: '+missing.join(', '));this.onEvent({event:'backend-error',error:this.failure.message});throw this.failure;}
    }
    const args=app.isPackaged?['--gui']:['-u',path.join(__dirname,'legacy_bridge.py'),'--gui'];
    const dataDir=this.options.dataDir||app.getPath('userData');
    const child=spawn(exe,args,{cwd:dataDir,env:{...process.env,DMT_DATA_DIR:dataDir,QT_QPA_PLATFORM:'offscreen',PYTHONFAULTHANDLER:'1',...this.options.env},windowsHide:true,stdio:['pipe','pipe','pipe']});
    this.child=child;let stderr='';
    this.options.diagnostics?.write('backend-start',{backend:this.options.label||'A',pid:child.pid});
    child.stderr.on('data',data=>{const value=data.toString();stderr=(stderr+value).slice(-12000);this.options.diagnostics?.stderr(this.options.label||'A',value);});
    child.stdin.on('error',()=>{});
    const fail=(error,details={})=>{if(this.child!==child)return;const diagnosticId=this.options.diagnostics?.write('backend-failure',{backend:this.options.label||'A',pid:child.pid,error:error.message,context:this.snapshot?.context,tasks:this.snapshot?.tasks,resources:this.lastResources,lastMethod:this.lastMethod,...details});this.child=null;this.busy=false;this.failure=error;for(const p of this.pending.values())p.reject(error);this.pending.clear();this.onEvent({event:'backend-error',error:error.message,diagnosticId,...details});};
    child.on('error',error=>fail(error));child.on('exit',(code,signal)=>fail(new Error(stderr||`Backend exited (${code}, ${signal||'no signal'})`),{exitCode:code,exitHex:Number.isInteger(code)?'0x'+(code>>>0).toString(16):null,signal}));
    createInterface({input:child.stdout}).on('line',line=>{
      if(this.child!==child)return;
      let m;try{m=JSON.parse(line);}catch{fail(new Error('Invalid backend response'));child.kill();return;}
      if(m.event){if(m.event==='snapshot'){this.snapshot=m.snapshot;this.busy=m.snapshot.busy;}if(m.event==='resources')this.lastResources=m.resources;if(m.event==='error')this.options.diagnostics?.write('backend-reported-error',{backend:this.options.label||'A',error:m.error,context:this.snapshot?.context});this.onEvent(m);return;}
      const p=this.pending.get(m.id);if(!p)return;this.pending.delete(m.id);m.error?p.reject(new Error(m.error)):p.resolve(m.result);
    });
  }
  call(method,args={}){if(this.updating)return Promise.reject(new Error('UPDATE_INSTALLING'));if(this.failure)return Promise.reject(this.failure);try{this.start();}catch(error){return Promise.reject(error);}if(!['gui.snapshot','gui.image-step'].includes(method))this.lastMethod=method;const id=++this.sequence;return new Promise((resolve,reject)=>{this.pending.set(id,{resolve,reject});this.child.stdin.write(JSON.stringify({id,method,args})+'\n');});}
  async retry(){this.close();this.failure=null;return this.call('gui.snapshot');}
  async stopForUpdate(){
    this.updating=true;
    const child=this.child;if(!child)return;
    await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>{child.removeListener('exit',done);this.child=child;reject(new Error('BACKEND_STOP_TIMEOUT'));},10000);
      const done=()=>{clearTimeout(timeout);resolve();};child.once('exit',done);
      this.child=null;child.stdin.end();
    });
    this.busy=false;
  }
  close(){const child=this.child;this.child=null;child?.kill();for(const p of this.pending.values())p.reject(new Error('Backend closed'));this.pending.clear();}
}
module.exports={GuiClient};
