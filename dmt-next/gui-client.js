const {spawn} = require('node:child_process');
const {createInterface} = require('node:readline');
const path = require('node:path');
const {app} = require('electron');
class GuiClient {
  constructor(onEvent) { this.onEvent=onEvent;this.pending=new Map();this.sequence=0;this.busy=false;this.snapshot=null;this.child=null; }
  start() {
    if(this.child)return;
    const exe=app.isPackaged?path.join(process.resourcesPath,'backend','dmt-service.exe'):(process.env.DMT_PYTHON||'python');
    const args=app.isPackaged?['--gui']:['-u',path.join(__dirname,'legacy_bridge.py'),'--gui'];
    const child=spawn(exe,args,{cwd:app.getPath('userData'),env:{...process.env,DMT_DATA_DIR:app.getPath('userData'),QT_QPA_PLATFORM:'offscreen'},windowsHide:true,stdio:['pipe','pipe','pipe']});
    this.child=child;let stderr='';
    child.stderr.on('data',data=>{stderr=(stderr+data.toString()).slice(-6000);});
    child.stdin.on('error',()=>{});
    const fail=error=>{if(this.child!==child)return;this.child=null;this.busy=false;for(const p of this.pending.values())p.reject(error);this.pending.clear();this.onEvent({event:'error',error:error.message});};
    child.on('error',fail);child.on('exit',code=>fail(new Error(stderr||`Backend exited (${code})`)));
    createInterface({input:child.stdout}).on('line',line=>{
      let m;try{m=JSON.parse(line);}catch{fail(new Error('Invalid backend response'));child.kill();return;}
      if(m.event){if(m.event==='snapshot'){this.snapshot=m.snapshot;this.busy=m.snapshot.busy;}this.onEvent(m);return;}
      const p=this.pending.get(m.id);if(!p)return;this.pending.delete(m.id);m.error?p.reject(new Error(m.error)):p.resolve(m.result);
    });
  }
  call(method,args={}){this.start();const id=++this.sequence;return new Promise((resolve,reject)=>{this.pending.set(id,{resolve,reject});this.child.stdin.write(JSON.stringify({id,method,args})+'\n');});}
  close(){const child=this.child;this.child=null;child?.kill();for(const p of this.pending.values())p.reject(new Error('Backend closed'));this.pending.clear();}
}
module.exports={GuiClient};
