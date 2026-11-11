const $=id=>document.getElementById(id);
let lang=localStorage.getItem('language')==='zh-TW'?'zh-TW':'ko';
let snapshot,dialogRequest=null,lastHash='',pending=0,imageViewer=null;
const dictionary=window.DMT_TRANSLATIONS||{};
const connectionButton=document.createElement('button');connectionButton.id='connection';document.querySelector('footer').prepend(connectionButton);
const phrases=Object.keys(dictionary).filter(k=>k.length>3).sort((a,b)=>b.length-a.length);
function t(value){const s=String(value??'');if(lang==='ko')return s;if(dictionary[s])return dictionary[s];return s.split('\n').map(line=>{if(dictionary[line])return dictionary[line];for(const key of phrases){if(line.includes(key))line=line.split(key).join(dictionary[key]);}return line;}).join('\n');}
const tr=(ko,zh)=>lang==='ko'?ko:zh;
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function error(e){$('alert').textContent=e?.message||String(e);}
async function action(id,action,value,extra={}){pending++;status();try{await window.studio.action({id,action,value,...extra});}catch(e){error(e);}finally{pending--;status();}}
function status(){$('activity').textContent=(pending||snapshot?.busy)?tr('처리 중…','處理中…'):tr('준비됨','就緒');}
function bind(n,w){n.dataset.control=w.id;n.disabled=!w.enabled;return n;}
function pager(w,parent){if(w.total<=200)return;const p=el('div','pager');const prev=el('button','', '‹');prev.disabled=w.start===0;prev.onclick=()=>action(w.id,'page',w.start-200);const next=el('button','','›');next.disabled=w.start+200>=w.total;next.onclick=()=>action(w.id,'page',w.start+200);p.append(prev,el('span','',`${w.start+1}–${Math.min(w.start+200,w.total)} / ${w.total}`),next);parent.append(p);}
function render(w){
 if(!w)return el('span');let n;
 switch(w.type){
 case 'tabs':{n=el('div','container');const bar=el('div','tabs');w.labels.forEach((label,i)=>{const b=el('button',i===w.index?'active':'',t(label));b.onclick=()=>action(w.id,'tab',i);bar.append(b);});n.append(bar,render(w.content));break;}
 case 'input':case 'date':{n=el('input');n.type=w.type==='date'?'date':'text';n.value=w.value;n.placeholder=t(w.placeholder);n.readOnly=w.readonly||false;n.onchange=()=>action(w.id,w.type==='date'?'date':'input',n.value);bind(n,w);break;}
 case 'select':{if(w.editable){n=el('input');n.value=w.value;const list=el('datalist');list.id='options-'+w.id;w.items.forEach(x=>{const opt=el('option');opt.value=x;list.append(opt);});n.setAttribute('list',list.id);n.onchange=()=>action(w.id,'edit-select',n.value);const wrap=el('div');wrap.append(bind(n,w),list);return wrap;}n=el('select');w.items.forEach((v,i)=>{const o=el('option','',t(v));o.value=i;n.append(o);});n.value=w.index;n.onchange=()=>action(w.id,'select',Number(n.value));bind(n,w);break;}
 case 'button':n=el('button','',t(w.text));bind(n,w);n.onclick=()=>action(w.id,'click');break;
 case 'check':case 'radio':{n=el('label','check');const input=bind(el('input'),w);input.type=w.type==='radio'?'radio':'checkbox';input.checked=w.checked;input.onchange=()=>action(w.id,'check',input.checked);n.append(input,el('span','',t(w.text)));break;}
 case 'label':n=el('div','label',t(w.text));break;
 case 'image':n=el('img','image');n.src=w.src;n.alt=tr('검사 이미지 — 클릭하여 확대','檢查影像 — 點擊放大');n.tabIndex=0;n.setAttribute('role','button');n.onclick=()=>{const photo=el('img','image-original');photo.src=w.src;photo.alt=n.alt;const overlay=modal(tr('불량 이미지 원본','不良影像原圖'),photo,()=>{imageViewer=null;overlay.remove();});imageViewer={overlay,photo,slot:w.imageSlot??0};document.body.append(overlay);};n.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();n.click();}};break;
 case 'text':{n=w.readonly?el('pre','text',t(w.text)):el('textarea','text');if(!w.readonly){n.value=w.text;n.onchange=()=>action(w.id,'text',n.value);bind(n,w);}break;}
 case 'progress':n=el('progress');if(w.max){n.max=w.max;n.value=w.value;}break;
 case 'table':{n=el('div','container');const wrap=el('div','table-wrap');const table=el('table');const head=el('thead');const hr=el('tr');w.headers.forEach((h,c)=>{const th=el('th');if(w.sortable){const b=el('button','',t(h)+(w.sortColumn===c?(w.sortDescending?' ▼':' ▲'):''));b.onclick=()=>action(w.id,'sort',w.sortColumn===c?!w.sortDescending:false,{column:c});th.append(b);}else th.textContent=t(h);hr.append(th);});head.append(hr);const body=el('tbody');w.rows.forEach((row,i)=>{const r=i+w.start;const tr=el('tr',r===w.selected?'selected':'');row.forEach((cell,c)=>{const td=el('td','',t(cell));td.onclick=()=>action(w.id,'table',null,{row:r,column:c});td.ondblclick=()=>action(w.id,'table',null,{row:r,column:c,double:true});tr.append(td);});body.append(tr);});table.append(head,body);wrap.append(table);n.append(wrap);pager(w,n);break;}
 case 'list':{n=el('div','container');const list=el('div','list');w.rows.forEach((row,i)=>{const item=el('div','list-item'+(row.selected?' selected':''));if(row.checkable){const check=el('input');check.type='checkbox';check.checked=row.checked;check.onclick=e=>e.stopPropagation();check.onchange=()=>action(w.id,'list-check',check.checked,{row:i+w.start});item.append(check);}item.append(el('span','',t(row.text)));item.onclick=()=>action(w.id,'list',i+w.start);list.append(item);});n.append(list);pager(w,n);break;}
 case 'tree':{n=el('div','list');const make=item=>{const box=el('div','tree-group');const line=el('button','',item.text.map(t).join('  |  '));line.onclick=()=>action(w.id,'tree',null,{route:item.route});box.append(line);item.children.forEach(c=>box.append(make(c)));return box;};w.rows.forEach(r=>n.append(make(r)));break;}
 default:{n=el('div',w.type==='group'?'group':w.type==='layout'?(w.direction==='row'?'row':'column'):'container');if(w.title)n.append(el('h2','',t(w.title)));const children=w.form?el('div','form'):w.horizontal?el('div','row'):n;(w.children||[]).filter(Boolean).forEach(c=>{const child=render(c);if(w.form&&c.formRole===2)child.classList.add('form-full');children.append(child);});if(children!==n)n.append(children);}
 }
 if(w.presentation)n.classList.add(w.presentation);
 if(w.id)n.dataset.key=w.id;return n;
}
function modal(title,content,onClose){const overlay=el('div','overlay');const box=el('section','modal');box.setAttribute('role','dialog');box.setAttribute('aria-modal','true');const head=el('div','modal-head');head.append(el('h2','',t(title)));if(onClose){const close=el('button','','×');close.setAttribute('aria-label',tr('닫기','關閉'));close.onclick=onClose;head.append(close);}box.append(head,content);overlay.append(box);return overlay;}
function renderDialogs(){
 const host=$('modals');host.replaceChildren();
 (snapshot?.dialogs||[]).forEach(d=>host.append(modal(d.title,render(d.content),()=>action(d.id,'reject'))));
 if(dialogRequest){const d=dialogRequest;const body=el('div');body.append(el('p','',t(d.text)));let input;
  const reply=async value=>{dialogRequest=null;renderDialogs();try{await window.studio.reply({id:d.id,value});}catch(e){dialogRequest=d;renderDialogs();error(e);}};
  if(['file','files','save','directory'].includes(d.kind)&&window.studio.showFileDialog){window.studio.showFileDialog(d,reply);return;}
  if(d.kind==='item'){input=el(d.editable?'input':'select');if(d.editable){input.value=d.items[d.current]||'';}else{d.items.forEach(v=>{const o=el('option','',v);o.value=v;input.append(o);});input.selectedIndex=d.current;}body.append(input);}
  const actions=el('div','modal-actions');
  if(d.kind==='message'){const names={Ok:tr('확인','確定'),Yes:tr('예','是'),No:tr('아니오','否'),Cancel:tr('취소','取消'),Close:tr('닫기','關閉'),Retry:tr('재시도','重試'),Abort:tr('중단','中止')};d.buttons.forEach(option=>{const b=el('button','',names[option.name]||option.name);b.onclick=()=>reply(option.value);actions.append(b);});}
  else{const cancel=el('button','',tr('취소','取消'));cancel.onclick=()=>reply(null);const ok=el('button','',tr('확인','確定'));ok.onclick=()=>reply(input.value);actions.append(cancel,ok);}
  body.append(actions);host.append(modal(d.title,body));
 }
}
function paint(force=false){
 if(!snapshot)return;const hash=JSON.stringify(snapshot)+lang;if(hash===lastHash&&!force)return;lastHash=hash;
 const focus=document.activeElement;const editing=focus?.dataset.control?{id:focus.dataset.control,value:focus.value,start:focus.selectionStart,end:focus.selectionEnd}:null;
 const scroll=new Map([...document.querySelectorAll('[data-key]')].filter(e=>e.scrollTop||e.scrollLeft).map(e=>[e.dataset.key,[e.scrollTop,e.scrollLeft]]));
 const root=snapshot.root;const nav=$('navigation');nav.replaceChildren();root.labels.forEach((label,i)=>{const b=el('button',i===root.index?'active':'',stepLabel(i,label));b.onclick=()=>action(root.id,'tab',i);nav.append(b);});
 $('title').textContent=stepLabel(root.index,root.labels[root.index]);renderYieldGuide(root);$('content').replaceChildren(render(root.content));renderDialogs();status();renderProgress();syncImageViewer();
 for(const [key,[top,left]] of scroll){const e=document.querySelector(`[data-key="${key}"]`);if(e){e.scrollTop=top;e.scrollLeft=left;}}
 if(editing){const input=document.querySelector(`[data-control="${editing.id}"]`);if(input&&['INPUT','TEXTAREA'].includes(input.tagName)&&input.type!=='checkbox'&&input.type!=='radio'){input.value=editing.value;input.focus({preventScroll:true});try{input.setSelectionRange(editing.start,editing.end);}catch{}}}
}
function labels(){document.documentElement.lang=lang;window.studio.language(lang);$('language').value=lang;$('connection').textContent=tr('NAS 연결 설정','NAS 連線設定');$('import').textContent=tr('모델 설정 / 캐시','機種設定 / 快取');$('settings').textContent=tr('설정 폴더 열기','開啟設定資料夾');$('updates').textContent=tr('업데이트 확인','檢查更新');}
$('connection').onclick=async()=>{
 try{const data=await window.studio.connection();const form=el('form','column');const inputs={};
 const fields=[['DMT_NAS_HOST','NAS 주소','NAS 位址','192.168.0.5'],['DMT_NAS_PORT','포트','連接埠','22'],['DMT_NAS_USER','사용자','使用者','admin'],['DMT_NAS_PASSWORD','비밀번호','密碼',''],['DMT_NAS_ROOT','데이터 루트','資料根目錄','/Pixel/ResultData/Data']];
 for(const [key,ko,zh,fallback] of fields){const label=el('label','column',tr(ko,zh));const input=el('input');input.type=key==='DMT_NAS_PASSWORD'?'password':'text';input.value=data.values[key]??fallback;if(key==='DMT_NAS_PASSWORD'&&data.hasPassword)input.placeholder=tr('비워 두면 기존 비밀번호 유지','留空以保留原有密碼');label.append(input);form.append(label);inputs[key]=input;}
 const info=el('p','',tr('저장하면 백엔드를 다시 연결합니다.','儲存後將重新連接後端。'));const save=el('button','',tr('저장','儲存'));save.type='submit';form.append(info,save);const overlay=modal(tr('NAS 연결 설정','NAS 連線設定'),form,()=>overlay.remove());document.body.append(overlay);
 form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await window.studio.saveConnection(Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value])));overlay.remove();}catch(e){info.textContent=e.message;save.disabled=false;}};
 }catch(e){error(e);}
};
$('language').onchange=()=>{lang=$('language').value;localStorage.setItem('language',lang);labels();paint(true);};
$('settings').onclick=()=>window.studio.settingsFolder().catch(error);
const updateLabels={development:['개발 실행','開發模式'],idle:['대기','待命'],checking:['업데이트 확인 중','正在檢查更新'],downloading:['다운로드 중','下載中'],current:['최신 버전','最新版本'],ready:['업데이트 준비됨 — 재시작 가능','更新已備妥 — 可重新啟動'],installing:['업데이트 및 재시작 중','正在更新並重新啟動'],error:['업데이트 확인 실패','更新檢查失敗']};
async function updateStatus(check=false){try{const u=await(check?window.studio.checkUpdate():window.studio.updateStatus());window.showUpdateStatus?.(u,check);const label=updateLabels[u.status]||[u.status,u.status];$('version').textContent=`v${u.version} · ${tr(...label)}${u.progress?' '+u.progress+'%':''}`;}catch(e){error(e);}}
$('updates').onclick=()=>updateStatus(true);
window.studio.onEvent(m=>{if(m.event==='snapshot'){snapshot=m.snapshot;paint();}else if(m.event==='dialog'){dialogRequest=m.dialog;renderDialogs();}else if(m.event==='error')error(m.error);});
labels();window.studio.snapshot().then(s=>{snapshot=s;paint();}).catch(error);updateStatus();setInterval(()=>updateStatus(),10000);

function renderProgress(){
 let host=$('task-progress');if(!host){host=el('section','group');host.id='task-progress';host.setAttribute('aria-label',tr('작업 진행 현황','工作進度'));$('content').before(host);}
 host.replaceChildren();const tasks=snapshot?.tasks||[];host.hidden=!tasks.length;
 const names={'NAS 연결 중':['NAS 연결 중','正在連接 NAS'],'폴더 검색 중':['폴더 검색 중','正在搜尋資料夾'],'현재 Trial 진행률':['현재 Trial 진행률','目前 Trial 進度'],'작업 진행 중':['작업 진행 중','工作進行中']};
 for(const task of tasks){const label=tr(...(names[task.stage]||names['작업 진행 중']));const percent=task.max>0?Math.floor(task.value/task.max*100):null;
 const text=label+(percent===null?'…':` · ${percent}% (${task.value}/${task.max})`)+tr(` · ${task.elapsed}초 경과`,` · 已經過 ${task.elapsed} 秒`);
 const bar=el('progress');bar.setAttribute('aria-label',label);if(task.max>0){bar.max=task.max;bar.value=task.value;}
 host.append(el('strong','',text),bar,el('p','',task.detail||tr('전체 수량을 확인하는 중입니다.','正在確認總數量。')));
 }
}

const yieldSteps=[{tab:0,number:'①',ko:'IVS 분석/조회',zh:'IVS 分析/查詢',hint:['IVS 스캔 또는 JSON 불러오기로 불량과 이미지를 확인하고, 필요한 IVS JSON을 저장하세요.','透過 IVS 掃描或載入 JSON 確認不良與影像，並儲存所需的 IVS JSON。']},{tab:3,number:'②',ko:'게더링 집계',zh:'整合統計',hint:['같은 모델과 LOT의 공정 데이터를 집계하고 결과를 저장하세요.','彙整相同機種與 LOT 的製程資料，並儲存結果。']},{tab:6,number:'③',ko:'수율 계산',zh:'良率計算',hint:['집계 파일과 IVS JSON, Strip당 수량 및 X-out 허용수를 확인한 뒤 수율을 계산하세요.','確認彙整檔案、IVS JSON、每片 Strip 數量與 X-out 容許數後，計算良率。']}];
function stepLabel(index,fallback){const step=yieldSteps.find(s=>s.tab===index);return step?`${step.number} ${tr(step.ko,step.zh)}`:t(fallback);}
function renderYieldGuide(root){
 let host=$('yield-guide');if(!host){host=el('section','yield-guide');host.id='yield-guide';$('content').before(host);}
 const current=yieldSteps.findIndex(s=>s.tab===root.index);host.hidden=current<0;host.replaceChildren();if(current<0)return;
 host.append(el('strong','',tr('수율 집계 진행 순서','良率彙整操作順序')));
 const steps=el('nav','yield-steps');steps.setAttribute('aria-label',tr('수율 집계 단계','良率彙整步驟'));
 yieldSteps.forEach((step,index)=>{if(index)steps.append(el('span','step-arrow','→'));const button=el('button',index===current?'current-step':'',stepLabel(step.tab));button.dataset.step=String(index+1);if(index===current)button.setAttribute('aria-current','step');button.onclick=()=>action(root.id,'tab',step.tab);steps.append(button);});
 host.append(steps,el('p','',tr(...yieldSteps[current].hint)));
 if(current<yieldSteps.length-1){const next=yieldSteps[current+1];const button=el('button','next-step',tr(`다음 단계: ${next.number} ${next.ko}로 이동`,`下一步：${next.number} ${next.zh}`));button.onclick=()=>action(root.id,'tab',next.tab);host.append(button);}
}

document.addEventListener('keydown',event=>{
 if(event.defaultPrevented||event.repeat||event.isComposing||event.ctrlKey||event.altKey||event.metaKey||snapshot?.root.index!==0||dialogRequest||[...document.querySelectorAll('.overlay')].some(o=>o!==imageViewer?.overlay))return;
 if(event.target.closest?.('input,textarea,select,[contenteditable="true"]'))return;
 const key=event.key.toLowerCase();if(key!=='a'&&key!=='d')return;
 event.preventDefault();window.studio.stepImage(key==='a'?-1:1).catch(error);
});

function syncImageViewer(){
 if(!imageViewer)return;let found;function walk(w){if(!w)return;if(w.type==='image'&&w.imageSlot===imageViewer.slot)found=w;for(const child of w.children||[])walk(child);walk(w.content);}walk(snapshot?.root);
 if(found){imageViewer.photo.src=found.src;imageViewer.photo.alt=tr('검사 이미지','檢查影像');}else{imageViewer.photo.removeAttribute('src');imageViewer.photo.alt=tr('이미지 불러오는 중…','正在載入影像…');}
}
