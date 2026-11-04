// Original engine operations; the original full UI remains available during migration.
const wb = document.createElement('section'); document.body.append(wb);
let wbLang = localStorage.getItem('language') || 'ko';
const wText = (ko,zh) => wbLang === 'zh-TW' ? zh : ko;
const scanFields = [['model','모델','機種'],['lot','LOT','LOT'],['trial_path','Trial 경로','Trial 路徑']];
const screens = [
 ['ivs.scan','IVS 분석','IVS 分析',scanFields],
 ['strip.scan','StripID 추출','擷取 StripID',scanFields],
 ['barcode.scan','전체 XML Barcode','全部 XML Barcode',scanFields],
 ['gather.parse','게더링 TXT 조회','Gathering TXT 查詢',[['path','TXT 파일','TXT 檔案','file'],['lot','LOT','LOT']]],
 ['strip.search','게더링 Strip 검색','搜尋 Gathering Strip',[['strip_id','StripID','StripID'],['model','모델','機種']]],
 ['inspection.stats','검사 통계','檢查統計',[['ivs_dirs','IVS 폴더 목록','IVS 資料夾清單','dirs'],['vrs_dirs','VRS 폴더 목록','VRS 資料夾清單','dirs'],['start','시작 날짜','開始日期','date'],['end','종료 날짜','結束日期','date']]],
 ['yield.calculate','수율 계산','良率計算',[['units_per_strip','Strip당 Unit','每片單元數','number'],['xout_limit','X-out 기준','X-out 標準','number'],['txt_paths','집계 TXT','彙總 TXT','files'],['vrs_paths','VRS TXT','VRS TXT','files'],['fvi_paths','FVI TXT','FVI TXT','files'],['ivs_paths','IVS TXT','IVS TXT','files'],['ivs_json_paths','IVS JSON','IVS JSON','files'],['strip_json','StripID JSON','StripID JSON','file'],['skipdata','Skipdata 폴더','Skipdata 資料夾','dir'],['ski','SKI','SKI','file']]],
 ['strip.validate','SKI 최종 검증','SKI 最終驗證',[['strip_json','StripID JSON','StripID JSON','file'],['txt_paths','TXT 파일','TXT 檔案','files'],['skipdata','Skipdata 폴더','Skipdata 資料夾','dir'],['ski','SKI','SKI','file']]],
 ['lm.preview','LM 미리보기','LM 預覽',[['json_path','검증 JSON','驗證 JSON','file'],['smk_folder','SMK 폴더','SMK 資料夾','dir'],['pmk_folder','PMK 폴더','PMK 資料夾','dir']]],
 ['tests.run','기존 테스트 실행','執行原有測試',[]]
];
let current = screens[0], lastResult, running = false;
function renderWorkbench() {
 wb.replaceChildren();
 const heading = document.createElement('h2'); heading.textContent = wText('분석 작업','分析工作'); wb.append(heading);
 const original = document.createElement('button'); original.textContent = wText('기존 전체 기능 화면 열기 (별도 창)','開啟完整原有功能（獨立視窗）'); original.onclick = () => window.dmt.openOriginal(); wb.append(original);
 const notice = document.createElement('p'); notice.textContent = wText('이미지·공정 일괄 집계·중복 선택·Excel·LM 실행은 기존 전체 기능 창에서 사용하세요.','影像、製程批次彙總、重複選擇、Excel 和 LM 執行請使用完整原有功能視窗。'); wb.append(notice);
 const nav = document.createElement('select'); screens.forEach(s => { const o = document.createElement('option'); o.value=s[0];o.textContent=wText(s[1],s[2]);nav.append(o); }); nav.value=current[0];nav.onchange=()=>{current=screens.find(s=>s[0]===nav.value);renderWorkbench();}; wb.append(nav);
 const form = document.createElement('form'); const saved=JSON.parse(localStorage.getItem('workbench:'+current[0])||'{}'); const inputs={};
 current[3].forEach(([key,ko,zh,type])=>{ const label=document.createElement('label');label.textContent=wText(ko,zh);const input=document.createElement('input');input.value=saved[key]||'';input.type=['date','number'].includes(type)?type:'text';label.append(input);inputs[key]=input;form.append(label);
 if(['file','files','dir','dirs'].includes(type)){const browse=document.createElement('button');browse.type='button';browse.textContent=wText('찾아보기','瀏覽');browse.onclick=async()=>{const paths=type.startsWith('dir')?[await window.dmt.chooseDirectory()]:await window.dmt.chooseFiles();if(paths.filter(Boolean).length)input.value=paths.filter(Boolean).join(';');};label.append(browse);}});
 const run=document.createElement('button');run.textContent=wText('실행','執行');run.disabled=running;form.append(run);const result=document.createElement('pre');result.style.cssText='max-height:500px;overflow:auto;white-space:pre-wrap';
 const save=document.createElement('button');save.type='button';save.textContent=wText('결과 JSON 저장','儲存結果 JSON');save.disabled=true;save.onclick=()=>window.dmt.saveResult(lastResult);form.append(save);
 form.onsubmit=async e=>{e.preventDefault();if(running)return;const values=Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,v.value]));localStorage.setItem('workbench:'+current[0],JSON.stringify(values));const args={};current[3].forEach(([key,,,type])=>{args[key]=['files','dirs'].includes(type)?values[key].split(';').map(v=>v.trim()).filter(Boolean):type==='number'?Number(values[key]):values[key];});running=true;run.disabled=true;nav.disabled=true;result.textContent=wText('처리 중…','處理中…');try{lastResult=await window.dmt.analyze(current[0],args);result.textContent=JSON.stringify(lastResult,null,2);save.disabled=false;}catch(error){result.textContent=error.message;}finally{running=false;run.disabled=false;nav.disabled=false;}};wb.append(form,result);
 const updates=document.createElement('button');updates.textContent=wText('업데이트 확인','檢查更新');const updateStatus=document.createElement('p');updates.onclick=async()=>{try{updateStatus.textContent=JSON.stringify(await window.dmt.checkUpdate());}catch(e){updateStatus.textContent=e.message;}};wb.append(updates,updateStatus);
}
document.querySelector('body > select').addEventListener('change', e=>{wbLang=e.target.value;if(!running)renderWorkbench();});
renderWorkbench();
