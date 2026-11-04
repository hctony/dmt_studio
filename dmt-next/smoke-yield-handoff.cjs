const {app,BrowserWindow}=require('electron');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=path.join(__dirname,'smoke-user-data','yield-handoff-ui');
fs.mkdirSync(path.join(root,'yield-handoff'),{recursive:true});
const revision=String(Date.now()),txt=path.join(root,'Gather_VRS_TEST.txt');
fs.writeFileSync(txt,'1,TEST-,TEST-01\nTEST-,TEST-01,EOS\n');
const fields={};for(const [name,value] of Object.entries({vrs_txt_in:txt,units_per_strip_in:'88',xout_limit_in:'13',skipdata_in:'\\\\nas\\TEST-\\BBT',ski_in:'\\\\nas\\TEST-.ski'}))fields[name]={value,revision};
fs.writeFileSync(path.join(root,'yield-handoff','latest.json'),JSON.stringify({active:'fixture',contexts:{fixture:{model:'TEST',lot:'TEST-',fields}}}));
app.setPath('userData',root);process.env.DMT_HEADLESS='1';app.disableHardwareAcceleration();require('./desktop');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{try{
 let win;for(let i=0;i<100;i++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}
 const js=s=>win.webContents.executeJavaScript(s);
 await js(`(async()=>{const s=await window.studio.snapshot();await window.studio.action({id:s.root.id,action:'tab',value:6});})()`);
 let values=[];for(let i=0;i<100;i++){values=await js("Array.from(document.querySelectorAll('#content input')).map(n=>n.value)");if(values.includes('88')&&values.includes('13')&&values.includes('TEST'))break;await delay(100);}
 assert(values.includes(txt));assert(values.includes('88'));assert(values.includes('13'));
 assert(await js("document.querySelector('#content').textContent.includes('TEST/TEST-')"));
 assert(await js("lang='zh-TW';paint();document.querySelector('#content').textContent.includes('重新載入最近完成的資料')&&document.querySelector('#content').textContent.includes('連結最近完成的資料')"));
 console.log(JSON.stringify({yieldAutofill:true,quantities:true,lotContext:true,traditionalChinese:true}));app.quit();
}catch(e){console.error(e);app.exit(1);}});
