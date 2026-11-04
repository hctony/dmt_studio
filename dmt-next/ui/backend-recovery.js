(() => {
 let popup=null;
 function show(message){
  if(popup){popup.detail.textContent=message;return;}
  const body=el('div','column');const description=el('p');const details=el('details');const summary=el('summary');const detail=el('pre','text',message);details.append(summary,detail);
  const actions=el('div','modal-actions');const close=el('button');const retry=el('button');
  body.append(description,details,actions);actions.append(close,retry);
  const overlay=modal('',body);overlay.id='backend-recovery';document.body.append(overlay);
  popup={overlay,description,summary,detail,close,retry};
  close.onclick=()=>{overlay.remove();popup=null;};
  retry.onclick=async()=>{retry.disabled=true;try{snapshot=await window.studio.retryBackend();$('alert').textContent='';paint(true);close.click();}catch(error){detail.textContent=error.message;}finally{retry.disabled=false;}};
  labels();
 }
 function labels(){if(!popup)return;popup.overlay.querySelector('h2').textContent=tr('분석 엔진 연결 오류','分析引擎連線錯誤');
  popup.description.textContent=tr('분석 엔진을 시작하지 못했습니다. 앱은 열린 상태로 유지되며 업데이트 확인도 사용할 수 있습니다. 설치가 끝난 뒤 다시 연결하세요. 계속 실패하면 최신 설치 파일을 다시 실행해 복구해 주세요.','無法啟動分析引擎。程式將保持開啟，仍可檢查更新。請在安裝完成後重新連接；若仍失敗，請重新執行最新版安裝檔修復。');
  popup.summary.textContent=tr('오류 상세','錯誤詳細資料');popup.close.textContent=tr('닫기','關閉');popup.retry.textContent=tr('다시 연결','重新連接');
 }
 window.showBackendFailure=show;
 window.studio.onEvent(m=>{if(m.event==='backend-error')show(m.error);});
 window.studio.backendStatus().then(s=>{if(s.error)show(s.error);}).catch(()=>{});
 $('language').addEventListener('change',labels);
})();
