/* Original Python Studio model profiles, stored only on this computer. */
$('import').onclick=async()=>{
 try{
  let data=await window.studio.modelSettings();
  const body=el('div','column');body.id='model-settings';
  const intro=el('p','',tr('기존 Studio JSON의 수량과 경로를 그대로 이어받습니다. 가져오기는 같은 모델의 항목을 갱신하며, 다른 모델은 유지하고 이전 설정을 백업합니다.','沿用舊版 Studio JSON 的數量與路徑。匯入時更新相同機種的欄位、保留其他機種，並備份原有設定。'));
  const picker=el('select');picker.id='saved-model';picker.setAttribute('aria-label',tr('저장된 모델','已儲存的機種'));
  const details=el('div','form');
  const fields=[['units_per_strip','Strip당 수량','每片 Strip 數量'],['xout_limit','X-out 허용수','X-out 容許數'],['vrs_txt_paths','VRS TXT 경로','VRS TXT 路徑'],['fvi_txt_paths','FVI TXT 경로','FVI TXT 路徑'],['ivs_txt_paths','IVS TXT 경로','IVS TXT 路徑'],['ivs_json_paths','IVS JSON 경로','IVS JSON 路徑'],['skipdata_path','BBT / Skipdata 폴더','BBT / Skipdata 資料夾'],['ski_path','최종 SKI 파일','最終 SKI 檔案']];
  const choice=el('label','check');const gathering=el('input');gathering.type='checkbox';gathering.id='model-gathering';choice.append(gathering,el('span','',tr('Gathering에도 모델·허용수·Skipdata·SKI 적용','同時將機種、容許數、Skipdata 與 SKI 套用至 Gathering')));
  const notice=el('p','',tr('저장된 경로에는 이전 LOT가 포함될 수 있습니다. 실제 파일과 LOT를 확인한 뒤 실행하세요. X/Y 좌표 수와 NAS 접속 정보는 이 JSON에 없어 변경하지 않습니다.','儲存的路徑可能包含先前的 LOT。執行前請確認檔案與 LOT。此 JSON 不含 X/Y 座標數與 NAS 連線資訊，因此不會變更這些設定。'));
  const message=el('p');message.setAttribute('role','status');message.id='model-message';
  const buttons=el('div','modal-actions');const load=el('button','',tr('기존 JSON 가져오기','匯入舊版 JSON'));load.id='model-import';const apply=el('button','',tr('선택 모델 적용','套用所選機種'));apply.id='model-apply';buttons.append(load,apply);
  body.append(intro,picker,details,choice,notice,message,buttons);
  const overlay=modal(tr('모델 설정 / 캐시','機種設定 / 快取'),body,()=>overlay.remove());document.body.append(overlay);
  function preview(){details.replaceChildren();const profile=data.profiles[picker.value]||{};for(const [key,ko,zh] of fields){const value=el('input');value.readOnly=true;value.value=Object.hasOwn(profile,key)?String(profile[key]):tr('(저장되지 않음)','（未儲存）');details.append(el('label','',tr(ko,zh)),value);}apply.disabled=!picker.value;}
  function refresh(){picker.replaceChildren();for(const model of Object.keys(data.profiles).sort()){const option=el('option','',model);option.value=model;picker.append(option);}if(Object.hasOwn(data.profiles,data.selected))picker.value=data.selected;preview();}
  picker.onchange=preview;refresh();
  load.onclick=async()=>{load.disabled=apply.disabled=true;try{const result=await window.studio.importSettings();if(result){data=result;refresh();message.textContent=tr(`${result.count}개 모델을 가져왔습니다. 현재 모델: ${result.selected}\n백업: ${result.backup}`,`已匯入 ${result.count} 個機種。目前機種：${result.selected}\n備份：${result.backup}`);}}catch(e){message.textContent=e.message;}finally{load.disabled=false;apply.disabled=!picker.value;}};
  apply.onclick=async()=>{apply.disabled=load.disabled=true;try{const result=await window.studio.applyModel({model:picker.value,gathering:gathering.checked});message.textContent=tr(`${result.model} 수량·경로 적용 완료${result.gathering?' (Gathering 포함)':''}.`,`${result.model} 數量與路徑已套用${result.gathering?'（包含 Gathering）':''}。`);const state=await window.studio.snapshot();await window.studio.action({id:state.root.id,action:'tab',value:6});}catch(e){message.textContent=e.message;}finally{apply.disabled=load.disabled=false;}};
 }catch(e){error(e);}
};
