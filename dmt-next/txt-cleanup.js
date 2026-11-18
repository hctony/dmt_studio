/* Only local A-PC IPC can configure permanent source-TXT retention. */
const fs=require('node:fs/promises'),path=require('node:path');
const DAY=24*60*60*1000;
function normalizedRoots(values){
 if(!Array.isArray(values)||!values.length||values.length>100)throw new Error('정리할 원본 폴더를 선택하세요. / 請選擇要清理的原始資料夾。');
 return [...new Set(values.map(value=>{if(typeof value!=='string'||!path.isAbsolute(value)||/[\r\n\0]/.test(value)||(path.resolve(value)===path.parse(path.resolve(value)).root&&!value.startsWith('\\\\')))throw new Error('원본 하위 폴더의 절대 경로를 입력하세요. / 請輸入原始子資料夾的絕對路徑。');return path.resolve(value);}))];
}
class TxtCleanup {
 constructor({root,collector,idle,barrier,diagnostics,now=Date.now}){Object.assign(this,{root,collector,idle,barrier,diagnostics,now});this.config={enabled:false,roots:[],nextAt:0};this.busy=false;this.current=null;this.preview=null;this.latest=null;this.polling=false;}
 async initialize(){try{const saved=JSON.parse(await fs.readFile(path.join(this.root,'txt-cleanup.json'),'utf8'));if(saved.approved===true&&saved.roots?.length)this.config={enabled:saved.enabled===true,roots:normalizedRoots(saved.roots),nextAt:Number(saved.nextAt)||0};this.latest=saved.latest||null;}catch(e){if(e.code!=='ENOENT')this.error=e.message;}return this.status();}
 async persist(){const target=path.join(this.root,'txt-cleanup.json');await fs.writeFile(target+'.tmp',JSON.stringify({...this.config,approved:true,latest:this.latest},null,2),{mode:0o600});await fs.rename(target+'.tmp',target);}
 status(){return {config:this.config,busy:this.busy,current:this.current,preview:this.preview,latest:this.latest,error:this.error||null,retentionDays:30,intervalHours:24};}
 async start(mode,roots,expectedPlan){
  if(this.busy||!this.idle())throw new Error('분석·대기 작업을 완료한 뒤 정리하세요. / 請先完成分析及排隊工作。');
  if(!this.collector?.maintenance)throw new Error('GO_COLLECTOR_UNAVAILABLE');
  const values=mode==='preview'?normalizedRoots(roots):this.preview?.roots;
  if(mode==='delete'&&(!this.preview||this.now()-this.preview.at>10*60*1000))throw new Error('삭제 전 미리보기를 다시 실행하세요. / 刪除前請重新預覽。');
  if(mode==='delete'&&expectedPlan&&this.preview.report.id!==expectedPlan)throw new Error('미리보기가 변경되었습니다. 다시 확인하세요. / 預覽已變更，請重新確認。');
  this.busy=true;this.barrier(true);this.error=null;
  if(mode==='preview')this.preview=null;
  try{const result=await this.collector.maintenance('/maintenance/'+mode,mode==='preview'?{roots:values}:{plan:this.preview.report.id});this.current={id:result.id,mode,roots:values,report:null};return this.status();}
  catch(e){this.busy=false;this.barrier(false);this.error=e.message;throw e;}
 }
 async poll(){
  if(!this.busy||!this.current||this.polling)return;this.polling=true;
  try{
   const job=await this.collector.maintenance('/jobs/'+this.current.id,undefined,'GET');this.current.report=job.maintenance||null;
   if(job.state!=='running'){
    const current=this.current,report=current.report||{error:'MAINTENANCE_REPORT_MISSING'};
    if(current.mode==='preview'&&!report.error&&job.state==='complete')this.preview={roots:current.roots,report,at:this.now()};
    if(current.mode==='delete'){this.preview=null;this.latest=report;const failed=!!report.error||report.failed||(report.roots||[]).some(h=>h.errors?.length);this.config.nextAt=this.now()+(failed?60*60*1000:DAY);}
    if(report.error)this.error=report.error;
    this.busy=false;this.current={...current,state:job.state};this.barrier(false);await this.persist();
    await this.collector.maintenance('/jobs/'+current.id,undefined,'DELETE').catch(()=>{});
   }
  }catch(e){this.error=e.message;const health=await this.collector.maintenance('/health',undefined,'GET').catch(()=>null);if(health?.active===0){this.busy=false;this.current=null;this.barrier(false);this.config.nextAt=this.now()+60*60*1000;await this.persist().catch(()=>{});}else{await this.cancel().catch(()=>{});}this.diagnostics?.write('txt-cleanup-error',{error:e.message});}
  finally{this.polling=false;}
 }
 async configure(enabled,roots){
  if(this.busy)throw new Error('정리 완료 후 설정하세요. / 請等待清理完成。');
  const previous={...this.config};
  if(enabled){const values=normalizedRoots(roots);if(!this.preview||this.now()-this.preview.at>10*60*1000||JSON.stringify(values)!==JSON.stringify(this.preview.roots)||this.preview.report.roots.some(h=>h.errors?.length))throw new Error('모든 경로의 미리보기를 완료한 뒤 자동 정리를 켜세요. / 請完成全部路徑預覽後再啟用自動清理。');this.config={enabled:true,roots:values,nextAt:this.now()};}
  else this.config.enabled=false;
  try{await this.persist();}catch(e){this.config=previous;throw e;}return this.status();
 }
 async tick(){
  await this.poll();if(this.busy||!this.config.enabled||this.now()<this.config.nextAt||!this.idle()||this.automatic)return;
  this.automatic=true;
  try{
   await this.start('preview',this.config.roots);while(this.busy){await new Promise(resolve=>setTimeout(resolve,250));await this.poll();}
   if(!this.preview||this.preview.report.error||this.error)throw new Error(this.error||'AUTO_PREVIEW_FAILED');
   // The durable A-PC policy authorizes new candidates in these exact roots.
   await this.start('delete');
  }catch(e){this.error=e.message;this.config.nextAt=this.now()+60*60*1000;await this.persist().catch(()=>{});}
  finally{this.automatic=false;}
 }
 async cancel(){if(this.current&&this.busy)await this.collector.maintenance('/jobs/'+this.current.id,undefined,'DELETE');return this.status();}
 close(){clearInterval(this.timer);}
 startTimer(){this.timer=setInterval(()=>this.tick().catch(e=>this.diagnostics?.write('txt-cleanup-error',{error:e.message})),1000);this.timer.unref();}
}
module.exports={TxtCleanup,normalizedRoots};
