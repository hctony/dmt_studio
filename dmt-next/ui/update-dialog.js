(() => {
  let state=null, popup=null, requested=false, localError='';
  const shown=new Set();
  function dismiss(){if(popup){popup.overlay.remove();popup=null;}requested=false;localError='';}
  function draw(){
    if(!state)return;
    const busy=!!(pending||snapshot?.busy||state.busy||dialogRequest);
    const key=`${state.available}:${state.status==='ready'?'ready':'available'}`;
    if(!popup){
      if(!requested && (!['downloading','ready'].includes(state.status)||shown.has(key)||busy||document.querySelector('.overlay')))return;
      const body=el('div','column');const message=el('p');const progress=el('progress');progress.max=100;
      const detail=el('small');const warning=el('p');warning.setAttribute('role','status');
      const actions=el('div','modal-actions');const later=el('button');later.id='update-later';later.onclick=dismiss;
      const install=el('button');install.id='update-restart';
      install.onclick=async()=>{
        install.disabled=true;localError='';
        try{await window.studio.installUpdate();}
        catch(error){
          localError=String(error.message).includes('UPDATE_BUSY')?'busy':String(error.message).includes('UPDATE_NOT_READY')?'not-ready':'error';
          try{state=await window.studio.updateStatus();}catch{}
          draw();
        }
      };
      actions.append(later,install);body.append(message,progress,detail,warning,actions);
      const overlay=modal('',body);overlay.id='update-dialog';document.body.append(overlay);
      popup={overlay,title:overlay.querySelector('h2'),message,progress,detail,warning,later,install};
      shown.add(key);requested=false;
    }
    if(state.status==='ready')shown.add(key);
    const texts={
      downloading:[`새 버전 ${state.available}을 다운로드하고 있습니다. 완료되면 업데이트 후 앱을 다시 시작할 수 있습니다.`,`正在下載新版本 ${state.available}。完成後即可更新並重新啟動程式。`],
      ready:[`새 버전 ${state.available}이 준비되었습니다. 업데이트를 설치한 뒤 앱을 자동으로 다시 시작합니다.`,`新版本 ${state.available} 已備妥。安裝更新後將自動重新啟動程式。`],
      installing:['업데이트를 설치하고 다시 시작합니다…','正在安裝更新並重新啟動…'],
      checking:['업데이트를 확인하고 있습니다…','正在檢查更新…'],
      current:['현재 최신 버전을 사용하고 있습니다.','目前已是最新版本。'],
      error:['업데이트를 완료하지 못했습니다. 잠시 후 업데이트 확인을 다시 눌러 주세요.','無法完成更新，請稍後再次按下檢查更新。'],
      development:['개발 실행에서는 자동 업데이트를 사용할 수 없습니다.','開發模式無法使用自動更新。']
    };
    popup.title.textContent=tr('DMT Studio 업데이트','DMT Studio 更新');
    popup.message.textContent=tr(...(texts[state.status]||texts.checking));
    popup.progress.hidden=state.status!=='downloading';popup.progress.value=state.progress||0;
    popup.detail.textContent=tr(`현재 버전 ${state.version}`,`目前版本 ${state.version}`)+(state.status==='downloading'?` · ${state.progress||0}%`:'');
    popup.warning.textContent=localError==='busy'||(busy&&state.status==='ready')?tr('작업을 완료하거나 중지한 뒤 업데이트할 수 있습니다.','請先完成或停止工作，再進行更新。'):localError==='not-ready'?tr('다운로드가 완료된 뒤 다시 시도해 주세요.','請在下載完成後重試。'):localError==='error'?tr('업데이트 실행에 실패했습니다. 다시 확인해 주세요.','無法執行更新，請重新檢查。'):'';
    popup.install.textContent=tr('업데이트 및 재시작','更新並重新啟動');
    popup.install.hidden=!['downloading','ready','installing'].includes(state.status);
    popup.install.disabled=state.status!=='ready'||busy;
    popup.later.textContent=['current','error','development'].includes(state.status)?tr('닫기','關閉'):tr('나중에','稍後');
    popup.later.disabled=state.status==='installing';
  }
  window.showUpdateStatus=(value,force=false)=>{state=value;requested=requested||force;draw();};
  window.studio.onUpdate(value=>window.showUpdateStatus(value));
  $('language').addEventListener('change',draw);
  setInterval(draw,1000);
})();
