(()=>{
 if(!window.studio.cleanupStatus)return;
 const button=el('button');button.id='txt-cleanup';document.querySelector('footer').prepend(button);
 const label=()=>button.textContent=tr('30일 지난 TXT 정리','清理超過 30 天的 TXT');label();$('language').addEventListener('change',label);
 button.onclick=async()=>{try{
  let state=await window.studio.cleanupStatus(),closed=false,dirty=false;
  const body=el('div','column');body.id='txt-cleanup-panel';
  body.append(el('p','',tr('A PC에서만 설정합니다. 선택한 원본 폴더 및 하위 폴더에서 생성·수정 시각 중 더 최근 시각이 30일을 넘긴 .txt만 정리합니다.','僅在 A 電腦設定。清理所選原始資料夾及子資料夾中，建立／修改時間較晚者已超過 30 天的 .txt。')),
   el('p','cleanup-warning',tr('원본을 영구 삭제하며 휴지통에 보관하지 않습니다. XML, RPT, SKI, 이미지와 앱 캐시는 지우지 않습니다. 중지해도 이미 삭제된 파일은 복구되지 않습니다.','永久刪除原始檔，不放入資源回收筒。不刪除 XML、RPT、SKI、影像及程式快取。停止不會復原已刪除檔案。')));
  const roots=el('textarea','text');roots.id='cleanup-roots';roots.rows=7;roots.value=state.config.roots.join('\n');roots.placeholder=tr('원본 TXT 폴더: 한 줄에 하나','原始 TXT 資料夾：每行一個');
  const load=el('button','',tr('IVS/FVI/VRS 설정 경로 불러오기','載入 IVS/FVI/VRS 設定路徑'));load.id='cleanup-load-roots';
  const controls=el('div','row'),preview=el('button','',tr('① 삭제 대상 미리보기','① 預覽刪除對象')),remove=el('button','',tr('② 미리보기 대상 지금 삭제','② 立即刪除預覽對象')),cancel=el('button','',tr('중지','停止'));
  preview.id='cleanup-preview';remove.id='cleanup-delete';cancel.id='cleanup-cancel';controls.append(preview,remove,cancel);
  const auto=el('button');auto.id='cleanup-auto';const policy=el('p'),status=el('p'),progress=el('progress');status.setAttribute('role','status');status.id='cleanup-status';progress.id='cleanup-progress';
  const summary=el('div','table-wrap'),samples=el('pre','text'),logs=el('button','',tr('정리 로그 폴더 열기','開啟清理紀錄資料夾'));logs.id='cleanup-logs';
  body.append(load,roots,controls,auto,policy,status,progress,summary,samples,logs);
  const overlay=modal(tr('원본 TXT 보관기간 정리','原始 TXT 保留期限清理'),body,()=>{if(state.busy){status.textContent=tr('정리를 완료하거나 중지한 뒤 닫으세요.','請完成或停止清理後再關閉。');return;}closed=true;clearInterval(timer);overlay.remove();});document.body.append(overlay);
  const values=()=>roots.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  async function run(fn){try{state=await fn();render();}catch(e){status.textContent=e.message;}}
  function render(){
   const report=state.current?.report||state.preview?.report||state.latest;
   roots.disabled=state.busy;load.disabled=state.busy;preview.disabled=state.busy;remove.disabled=state.busy||!state.preview||dirty;cancel.disabled=!state.busy;auto.disabled=state.busy||(!state.config.enabled&&(!state.preview||dirty));
   auto.textContent=state.config.enabled?tr('자동 정리 끄기','停用自動清理'):tr('자동 정리 켜기 (24시간마다 유휴 시)','啟用自動清理（每 24 小時閒置時）');
   policy.textContent=state.config.enabled?tr('자동 정리 사용 중 · 분석/대기 작업이 있으면 연기합니다. 다음 예정: ','已啟用自動清理，分析／排隊時延後。下次預定：')+new Date(state.config.nextAt).toLocaleString():tr('자동 정리 꺼짐. 미리보기 확인 후 켤 수 있습니다.','自動清理已停用。請預覽確認後啟用。');
   const incomplete=state.error||report?.failed||report?.error||(report?.roots||[]).some(h=>h.errors?.length);
   status.textContent=(state.busy?tr('처리 중','處理中'):incomplete?tr('확인 필요','需確認'):report?.mode==='delete'?tr('정리 완료','清理完成'):report?tr('미리보기 완료','預覽完成'):tr('대기','待命'))+(report?` · ${tr('대상','對象')} ${report.count} TXT · ${(report.bytes/1024/1024).toFixed(2)} MB · ${tr('삭제/건너뜀/실패','刪除／略過／失敗')} ${report.deleted||0}/${report.skipped||0}/${report.failed||0}\n${tr('삭제 기준 (UTC)','刪除界線 (UTC)')}: ${report.cutoff}`:'')+(state.busy&&report?.active?'\n'+report.active:'')+(state.error?'\n'+state.error:'');
   progress.hidden=!state.busy;if(report?.mode==='delete'&&report.count){progress.max=report.count;progress.value=(report.deleted||0)+(report.skipped||0)+(report.failed||0);}else{progress.removeAttribute('value');progress.removeAttribute('max');}
   summary.replaceChildren();if(report){const table=el('table'),head=el('tr');for(const title of [tr('원본 경로','原始路徑'),tr('대상 TXT','對象 TXT'),'MB',tr('오류','錯誤')])head.append(el('th','',title));table.append(head);
    for(const host of report.roots||[]){const row=el('tr');for(const value of [host.path,host.candidates,(host.bytes/1024/1024).toFixed(2),(host.errors||[]).join('\n')])row.append(el('td','',String(value)));table.append(row);}summary.append(table);
    samples.textContent=tr('대상/결과 샘플 최대 50개. 전체 내역은 로그에서 확인하세요.','最多顯示 50 筆對象／結果範例，完整紀錄請查看日誌。')+'\n'+(report.samples||[]).map(f=>`${f.state||'candidate'} · ${f.path} · ${f.size} bytes · ${f.last_activity_at||''}${f.error?' · '+f.error:''}`).join('\n');
   }
  }
  roots.oninput=()=>{dirty=true;render();};load.onclick=()=>run(async()=>{const sources=await window.studio.cleanupRoots();roots.value=[...new Set(sources.map(s=>s.path))].join('\n');dirty=true;return state;});
  preview.onclick=()=>{dirty=false;run(()=>window.studio.cleanupPreview(values()));};remove.onclick=()=>run(()=>window.studio.cleanupDelete());cancel.onclick=()=>run(()=>window.studio.cleanupCancel());
  auto.onclick=()=>run(()=>window.studio.cleanupConfigure({enabled:!state.config.enabled,roots:values()}));logs.onclick=()=>window.studio.cleanupLogs().catch(error);
  const timer=setInterval(async()=>{if(closed)return;try{state=await window.studio.cleanupStatus();render();}catch(e){status.textContent=e.message;}},1000);
  render();
 }catch(e){error(e);}};
})();
