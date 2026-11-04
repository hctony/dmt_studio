/* Local LOT evidence and recommendations; no remote telemetry. */
const workflowButton=el('button');
workflowButton.id='workflow';
document.querySelector('footer').prepend(workflowButton);
let workflowState=null;
const wf=(method,args={})=>window.studio.workflow(method,args);
const fieldNames={
  trial_path_in:['Trial 경로','Trial 路徑'],path_in:['데이터 경로','資料路徑'],ip_in:['설비 IP 범위','設備 IP 範圍'],
  units_per_strip_in:['Strip당 Unit 수','每 Strip 的 Unit 數'],xout_limit_in:['X-out 한도','X-out 上限'],
  x_count_in:['X 개수','X 數量'],y_count_in:['Y 개수','Y 數量'],allowance_in:['허용 불량 수','允許不良數'],fill_code_in:['채움 코드','填補代碼'],exclude_codes_in:['제외 코드','排除代碼'],
  txt_in:['집계 TXT','彙總 TXT'],json_in:['검증 JSON','驗證 JSON'],strip_json_in:['Strip JSON','Strip JSON'],
  vrs_txt_in:['VRS TXT','VRS TXT'],fvi_txt_in:['FVI TXT','FVI TXT'],ivs_txt_in:['IVS TXT','IVS TXT'],ivs_json_in:['IVS JSON','IVS JSON'],
  exclude_json_in:['제외 Strip JSON','排除 Strip JSON'],skipdata_in:['BBT / Skipdata 폴더','BBT / Skipdata 資料夾'],ski_in:['SKI 파일','SKI 檔案'],
  smk_folder_in:['SMK 폴더','SMK 資料夾'],pmk_folder_in:['PMK 폴더','PMK 資料夾'],
  compare_xml_json_in:['비교 XML JSON','比對 XML JSON'],compare_strip_json_in:['비교 Strip JSON','比對 Strip JSON'],
  ivs_ip_in:['IVS 설비 IP','IVS 設備 IP'],ivs_path_in:['IVS 데이터 경로','IVS 資料路徑'],ivs_unit_ip_in:['Unit IVS 설비 IP','Unit IVS 設備 IP'],
  ivs_unit_path_in:['Unit IVS 데이터 경로','Unit IVS 資料路徑'],vrs_ip_in:['VRS 설비 IP','VRS 設備 IP'],vrs_path_in:['VRS 데이터 경로','VRS 資料路徑']
};
function workflowLabel(){workflowButton.textContent=workflowState?.active?tr('● LOT 기록 중 · 추천','● LOT 記錄中 · 建議'):tr('LOT 테스트 기록 · 추천','LOT 測試紀錄 · 建議');}
async function refreshWorkflow(){try{workflowState=await wf('status');workflowLabel();}catch(e){error(e);}}
workflowButton.onclick=async()=>{
 try{
  await refreshWorkflow();
  const body=el('div','column');
  const info=el('p','',tr('LOT별로 기록을 시작하고 테스트 후 결과를 확인해 주세요. 같은 모델의 서로 다른 LOT 3개에서 일치한 입력은 확실한 추천으로 표시합니다.','請為每個 LOT 開始記錄，測試後確認結果。同機種的 3 個不同 LOT 一致的輸入會標示為可靠建議。'));
  body.append(info);
  const current=workflowState.active;
  const form=el('div','row');const model=el('input');model.placeholder=tr('모델','機種');model.value=current?.model||localStorage.getItem('workflow-model')||'';
  const lot=el('input');lot.placeholder='LOT';lot.value=current?.lot||'';form.append(model,lot);body.append(form);
  const progress=el('p');const updateProgress=()=>{const count=workflowState.successfulByModel[model.value.trim()]||0;progress.textContent=tr(`이 모델의 성공 LOT: ${count} / 최소 3개 · 각 입력의 일치 여부도 확인합니다.`,`此機種成功 LOT：${count} / 至少 3 個 · 同時檢查各欄位是否一致。`);};model.addEventListener('input',updateProgress);updateProgress();body.append(progress);
  const feedback=el('p','workflow-feedback');body.append(feedback);
  const controls=el('div','row');body.append(controls);
  const note=el('textarea');note.placeholder=tr('현장 메모: 예상과 달랐던 결과, 경로 문제 등','現場備註：非預期結果、路徑問題等');
  const overlay=modal(tr('LOT 테스트 기록과 스마트 입력','LOT 測試紀錄與智慧輸入'),body,()=>overlay.remove());document.body.append(overlay);
  async function run(button,task){button.disabled=true;try{await task();await refreshWorkflow();}catch(e){feedback.textContent=e.message;}finally{button.disabled=false;}}
  if(current){
   model.readOnly=true;lot.readOnly=true;
   feedback.textContent=tr(`기록 중: ${current.model} / ${current.lot}`,`記錄中：${current.model} / ${current.lot}`);
   body.append(note);
   for(const [outcome,ko,zh] of [['success','테스트 성공 확인','確認測試成功'],['failed','실패로 기록 종료','以失敗結束記錄']]){
    const b=el('button','',tr(ko,zh));b.onclick=()=>run(b,async()=>{await wf('finish',{outcome,note:note.value});overlay.remove();});controls.append(b);
   }
  }else{
   const start=el('button','',tr('이 LOT 기록 시작','開始記錄此 LOT'));start.onclick=()=>run(start,async()=>{await wf('start',{model:model.value,lot:lot.value});localStorage.setItem('workflow-model',model.value);overlay.remove();});controls.append(start);
  }
  const autoLabel=el('label','check');const auto=el('input');auto.type='checkbox';auto.checked=workflowState.preferences.autofill;
  auto.onchange=async()=>{try{await wf('preferences',{autofill:auto.checked});}catch(e){auto.checked=!auto.checked;feedback.textContent=e.message;}};
  autoLabel.append(auto,el('span','',tr('새 LOT 기록 시작 시 확실한 추천으로 빈 칸 자동입력','開始新 LOT 記錄時，以可靠建議自動填入空白欄位')));body.append(autoLabel);
  body.append(el('p','',tr('자동입력은 데이터를 처리하거나 파일을 수정하지 않습니다. 현재 화면의 모델·LOT는 기록 시작 시 적용됩니다. 다른 화면은 대상 모델·LOT를 맞춘 후 추천을 적용하세요.','自動填入不會執行處理或修改檔案。開始記錄時會設定目前畫面的機種與 LOT；其他畫面請先設定相符的機種與 LOT 再套用建議。')));
  const recommend=el('button','',tr('이 모델·LOT의 추천 보기','查看此機種與 LOT 的建議'));body.append(recommend);
  const recommendations=el('div','column');body.append(recommendations);
  recommend.onclick=()=>run(recommend,async()=>{
   if(!model.value.trim()||!lot.value.trim())throw new Error(tr('모델과 LOT를 입력하세요.','請輸入機種與 LOT。'));
   const items=await wf('recommend',{model:model.value,lot:lot.value});recommendations.replaceChildren();
   if(!items.length){recommendations.append(el('p','',tr('추천 근거가 아직 없습니다. 먼저 LOT 기록을 쌓아 주세요.','尚無建議依據，請先累積 LOT 紀錄。')));return;}
   const selected=[];
   for(const item of items){
    const card=el('section','workflow-card');const label=el('label','check');const check=el('input');check.type='checkbox';check.checked=item.confidence==='high';selected.push({check,key:item.key});
    label.append(check,el('strong','',`${t(item.tab)} · ${item.mode?item.mode+' · ':''}${tr(...(fieldNames[item.field]||[item.field,item.field]))}`));card.append(label);
    card.append(el('p','',tr(`추천: ${item.value}`,`建議：${item.value}`)));
    card.append(el('small','',tr(`현재: ${item.current||'(빈 칸)'} · ${item.support}/${item.total} LOT 일치 · ${item.confidence==='high'?'확실한 반복 패턴':'확인 필요'}`,`目前：${item.current||'（空白）'} · ${item.support}/${item.total} LOT 一致 · ${item.confidence==='high'?'可靠重複模式':'需要確認'}`)));
    const evidence=el('details');evidence.append(el('summary','',tr('추천 근거','建議依據')),el('p','',tr(`패턴: ${item.pattern}\n사용 LOT: ${item.lots.join(', ')}\n경로 존재 여부는 아직 검증하지 않았습니다.`,`模式：${item.pattern}\n使用 LOT：${item.lots.join(', ')}\n尚未驗證路徑是否存在。`)));card.append(evidence);recommendations.append(card);
   }
   const replaceLabel=el('label','check');const replace=el('input');replace.type='checkbox';replaceLabel.append(replace,el('span','',tr('선택한 항목의 기존 값도 교체','也取代所選欄位的現有值')));recommendations.append(replaceLabel);
   const apply=el('button','',tr('선택한 추천 자동입력','自動填入所選建議'));apply.onclick=()=>run(apply,async()=>{
    const r=await wf('apply',{model:model.value,lot:lot.value,keys:selected.filter(x=>x.check.checked).map(x=>x.key),overwrite:replace.checked});
    feedback.textContent=tr(`${r.applied.length}개 입력 완료, ${r.skipped.length}개 유지됨. 기존 값·모델·LOT 일치 여부를 확인하세요.`,`${r.applied.length} 項已填入，${r.skipped.length} 項保留。請確認現有值、機種與 LOT 是否相符。`);
   });recommendations.append(apply);
  });
  const outcomeNames={success:['성공 확인','已確認成功'],failed:['실패','失敗'],recording:['기록 중','記錄中'],interrupted:['중단됨','已中斷']};
  const history=el('details');history.append(el('summary','',tr(`최근 기록 · 성공 LOT ${workflowState.successfulLots}개`,`近期紀錄 · 成功 LOT ${workflowState.successfulLots} 個`)));
  workflowState.runs.forEach(r=>history.append(el('p','',`${r.model} / ${r.lot} · ${tr(...(outcomeNames[r.outcome]||[r.outcome,r.outcome]))} · ${new Date(r.started).toLocaleString()}${r.note?'\n'+r.note:''}`)));body.append(history);
  const exportButton=el('button','',tr('분석용 로그 JSON 내보내기','匯出分析用紀錄 JSON'));exportButton.onclick=()=>run(exportButton,async()=>{const path=await window.studio.exportWorkflows();if(path)feedback.textContent=tr('저장 완료: ','已儲存：')+path;});body.append(exportButton);
  body.append(el('small','',tr('로그는 이 PC에 저장됩니다. 내보낸 파일에는 모델·LOT·선택 경로·입력·작업 결과 요약이 포함되며, NAS 비밀번호와 .env는 포함하지 않습니다.','紀錄保存在此電腦。匯出檔包含機種、LOT、所選路徑、輸入及工作結果摘要，不含 NAS 密碼與 .env。')));
  if(workflowState.error)feedback.textContent=workflowState.error;
 }catch(e){error(e);}
};
$('language').addEventListener('change',workflowLabel);
refreshWorkflow();
