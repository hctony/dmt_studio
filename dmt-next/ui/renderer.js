const fields = ['name', 'model', 'lot', 'nasRoot', 'backupDir', 'skipdataDir', 'outputDir'];
const $ = id => document.getElementById(id);
const translations = { '작업 프로필':'工作設定檔', '모델과 LOT, 자주 사용하는 경로를 프로필별로 저장합니다.':'按設定檔儲存機種、LOT 和常用路徑。', '기존 모델 설정 가져오기':'匯入現有機種設定', '저장된 프로필':'已儲存的設定檔', '새 프로필':'新增設定檔', '선택 삭제':'刪除所選設定檔', '프로필 이름':'設定檔名稱', '모델':'機種', 'NAS 루트 경로':'NAS 根目錄', '게더링 백업 폴더':'Gathering 備份資料夾', 'BBT / Skipdata 폴더':'BBT / Skipdata 資料夾', '결과 저장 폴더':'結果儲存資料夾', '찾아보기':'瀏覽', '저장':'儲存', '이미 존재하는 프로필 이름입니다.':'設定檔名稱已存在。', '저장했습니다.':'已儲存。', '삭제했습니다.':'已刪除。' };
let language = localStorage.getItem('language') === 'zh-TW' ? 'zh-TW' : 'ko';
function translate() { document.documentElement.lang = language; document.querySelectorAll('[data-original]').forEach(el => { el.textContent = language === 'zh-TW' ? (translations[el.dataset.original] || el.dataset.original) : el.dataset.original; }); }
const languageSelect = document.createElement('select'); languageSelect.innerHTML = '<option value="ko">한국어</option><option value="zh-TW">繁體中文</option>'; languageSelect.value = language; languageSelect.setAttribute('aria-label', 'Language / 語言'); document.body.prepend(languageSelect);
languageSelect.onchange = () => { language = languageSelect.value; localStorage.setItem('language', language); translate(); };
document.querySelectorAll('h1,p,button,label').forEach(el => { if (translations[el.textContent]) el.dataset.original = el.textContent; });
let state = { profiles: [], active: '' };
$('import').onclick = async () => { try { const result = await window.dmt.importModelSettings(); if (result) $('status').textContent = `${result.count}개 모델 설정을 보관했습니다. 분석 기능과의 연결은 아직 구현되지 않았습니다.`; } catch(e) { $('status').textContent = `가져오기 실패: ${e.message}`; } };
for (const [id, label] of [['backupDir','게더링 백업 폴더'],['skipdataDir','BBT / Skipdata 폴더'],['outputDir','결과 저장 폴더']]) {
  const lab = document.createElement('label'); lab.htmlFor = id; lab.textContent = label;
  lab.dataset.original = label;
  const row = document.createElement('div'); row.className = 'row';
  const input = document.createElement('input'); input.id = id;
  const button = document.createElement('button'); button.type = 'button'; button.textContent = '찾아보기';
  button.dataset.original = '찾아보기';
  button.onclick = async () => { const path = await window.dmt.chooseDirectory(); if (path) input.value = path; };
  row.append(input, button); $('paths').append(lab, row);
}
function show(profile = {}) { fields.forEach(id => $(id).value = profile[id] || ''); }
function refresh() { $('profiles').replaceChildren(); for (const p of state.profiles) { const option = document.createElement('option'); option.value = p.name; option.textContent = p.name; $('profiles').append(option); } $('profiles').value = state.active; show(state.profiles.find(p => p.name === state.active)); }
$('profiles').onchange = () => { state.active = $('profiles').value; show(state.profiles.find(p => p.name === state.active)); };
$('new').onclick = () => { state.active = ''; $('profiles').selectedIndex = -1; show(); $('name').focus(); };
$('form').onsubmit = async e => { e.preventDefault(); const p = Object.fromEntries(fields.map(id => [id, $(id).value.trim()])); const old = state.active; if (state.profiles.some(item => item.name === p.name && item.name !== old)) { $('status').textContent = '이미 존재하는 프로필 이름입니다.'; return; } const next = {profiles: state.profiles.filter(item => item.name !== old).concat(p), active:p.name}; try { await window.dmt.save(next); state = next; refresh(); $('status').textContent = '저장했습니다.'; } catch(err) { $('status').textContent = `저장 실패: ${err.message}`; } };
$('delete').onclick = async () => { if (!state.active) return; const next = { profiles: state.profiles.filter(p => p.name !== state.active), active: '' }; try { await window.dmt.save(next); state = next; refresh(); $('status').textContent = '삭제했습니다.'; } catch(err) { $('status').textContent = `삭제 실패: ${err.message}`; } };
translate();
const cacheBox = document.createElement('section');
const cacheTitle = document.createElement('h2'); cacheTitle.textContent = 'Backend cache / 後端快取';
const cacheOutput = document.createElement('pre'); cacheOutput.style.whiteSpace = 'pre-wrap';
const cacheRead = document.createElement('button'); cacheRead.textContent = '캐시 확인 / 檢查快取';
const cacheClear = document.createElement('button'); cacheClear.textContent = '캐시 비우기 / 清除快取';
const displayCache = async clear => { try { cacheOutput.textContent = JSON.stringify(await (clear ? window.dmt.clearCache() : window.dmt.cacheStatus()), null, 2); } catch(e) { cacheOutput.textContent = e.message; } };
cacheRead.onclick = () => displayCache(false); cacheClear.onclick = () => displayCache(true);
cacheBox.append(cacheTitle, cacheRead, cacheClear, cacheOutput); document.body.append(cacheBox);
window.dmt.load().then(s => { state = s; refresh(); }).catch(e => $('status').textContent = `불러오기 실패: ${e.message}`);
