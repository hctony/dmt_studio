const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
class Diagnostics {
 constructor(root,secrets=()=>[]){this.directory=path.join(root,'diagnostics');this.secrets=secrets;this.lastError=null;try{fs.mkdirSync(this.directory,{recursive:true});}catch(error){this.lastError=error.message;}}
 clean(value){if(typeof value==='string'){let text=value.slice(-16000).replace(/Bearer\s+[A-Za-z0-9._~-]+/gi,'Bearer [REDACTED]').replace(/([?&](?:token|key|ticket)=)[^&\s]+/gi,'$1[REDACTED]');for(const secret of this.secrets())if(secret&&secret.length>=4)text=text.split(secret).join('[REDACTED]');return text;}if(Array.isArray(value))return value.slice(0,50).map(v=>this.clean(v));if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,/password|token|authorization|secret|accesskey/i.test(key)?'[REDACTED]':this.clean(item)]));return value;}
 append(name,text){try{const file=path.join(this.directory,name);if(fs.existsSync(file)&&fs.statSync(file).size>3*1024*1024)fs.renameSync(file,file+'.previous');fs.appendFileSync(file,text);}catch(e){this.lastError=e.message;}}
 write(kind,data={}){const id=crypto.randomUUID();this.append('events.jsonl',JSON.stringify({id,at:new Date().toISOString(),kind,...this.clean(data)})+'\n');return id;}
 stderr(label,text){this.append('backend-'+String(label).replace(/[^A-Za-z0-9_-]/g,'_')+'.log',this.clean(text));}
 export(extra={}){const logs={};for(const item of fs.readdirSync(this.directory,{withFileTypes:true})){if(!item.isFile()||!/(\.jsonl|\.log)(\.previous)?$/.test(item.name))continue;const file=path.join(this.directory,item.name);const size=fs.statSync(file).size;const fd=fs.openSync(file,'r');try{const data=Buffer.alloc(Math.min(size,1024*1024));fs.readSync(fd,data,0,data.length,Math.max(0,size-data.length));logs[item.name]=this.clean(data.toString('utf8'));}finally{fs.closeSync(fd);}}return {schema:'dmt-diagnostics-v1',generatedAt:new Date().toISOString(),...this.clean(extra),logs,loggingError:this.lastError};}
}
function watchWindow(win,label,diagnostics,onGone){const safeUrl=()=>{try{const u=new URL(win.webContents.getURL());return u.origin+u.pathname;}catch{return '';}};
 win.webContents.on('render-process-gone',(_event,details)=>{const id=diagnostics.write('renderer-gone',{window:label,url:safeUrl(),...details});try{Promise.resolve(onGone?.(details,id)).catch(error=>diagnostics.write('renderer-recovery-error',{error:error.message}));}catch(error){diagnostics.write('renderer-recovery-error',{error:error.message});}});
 win.webContents.on('unresponsive',()=>diagnostics.write('renderer-unresponsive',{window:label,url:safeUrl()}));
 win.webContents.on('responsive',()=>diagnostics.write('renderer-responsive',{window:label}));
 win.webContents.on('did-fail-load',(_event,code,description,_url,isMainFrame)=>{if(isMainFrame)diagnostics.write('client-load-failed',{window:label,code,description,url:safeUrl()});});
 win.on('close',()=>diagnostics.write('window-close-request',{window:label,url:safeUrl()}));
 win.on('closed',()=>diagnostics.write('window-closed',{window:label}));
}
module.exports={Diagnostics,watchWindow};
