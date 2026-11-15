const {spawn}=require('node:child_process');
const {createInterface}=require('node:readline');
const crypto=require('node:crypto'),path=require('node:path'),fs=require('node:fs');
async function startCollector(app,diagnostics){
 const exe=app.isPackaged?path.join(process.resourcesPath,'collector','dmt-collector.exe'):path.join(__dirname,'collector-bin','dmt-collector.exe');
 if(app.isPackaged)process.env.DMT_COLLECTOR_REQUIRED='1';
 if(!fs.existsSync(exe)){if(app.isPackaged)throw new Error('COLLECTOR_FILES_MISSING');return {busy:false,close(){},async drain(){}};}
 const token=crypto.randomBytes(32).toString('hex');let child,closing=false,port='0',readyUrl;
 const service={busy:false,close(){closing=true;clearInterval(timer);child?.kill();},async drain(){const response=await fetch(readyUrl+'/health',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(3000)});const state=await response.json();if(state.active)throw new Error('UPDATE_BUSY');}};
 async function launch(){await new Promise((resolve,reject)=>{
  const proc=spawn(exe,[],{env:{...process.env,DMT_COLLECTOR_TOKEN:token,DMT_COLLECTOR_PORT:port,DMT_COLLECTOR_PARENT_PIPE:'1',DMT_COLLECTOR_STORE:path.join(app.getPath('userData'),'cache','collector')},windowsHide:true,stdio:['pipe','pipe','pipe']});child=proc;
  const timeout=setTimeout(()=>{proc.kill();reject(new Error('COLLECTOR_START_TIMEOUT'));},15000);
  proc.once('error',error=>{clearTimeout(timeout);reject(error);});
  proc.stderr.on('data',data=>diagnostics?.stderr('collector',data.toString()));
  createInterface({input:proc.stdout}).once('line',line=>{try{
   const data=JSON.parse(line),url=new URL(data.url);if(url.hostname!=='127.0.0.1'||url.protocol!=='http:')throw new Error('Invalid collector address');
   readyUrl=url.origin;port=url.port;process.env.DMT_COLLECTOR_URL=readyUrl;process.env.DMT_COLLECTOR_TOKEN=token;process.env.DMT_COLLECTOR_REQUIRED='1';clearTimeout(timeout);resolve();
  }catch(error){clearTimeout(timeout);proc.kill();reject(error);}});
  proc.on('exit',(code)=>{clearTimeout(timeout);service.busy=false;diagnostics?.write('collector-exit',{code});reject(new Error('COLLECTOR_EXITED'));if(!closing)setTimeout(()=>launch().catch(error=>diagnostics?.write('collector-restart-failed',{error:error.message})),1000).unref();});
 });}
 const timer=setInterval(async()=>{if(!readyUrl||closing)return;try{const r=await fetch(readyUrl+'/health',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(2000)});service.busy=!!(await r.json()).active;}catch{}},1000);timer.unref();
 try{await launch();return service;}catch(error){service.close();throw error;}
}
module.exports={startCollector};
