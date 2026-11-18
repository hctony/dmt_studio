/* Destructive checks use generated fixtures only; never production shares. */
const electron=require('electron'),{app,BrowserWindow,dialog}=electron;
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const root=path.join(__dirname,'smoke-user-data','cleanup-'+Date.now()),sources=path.join(root,'sources');fs.mkdirSync(sources,{recursive:true});
app.setPath('userData',root);process.env.DMT_HEADLESS='1';process.env.DMT_SETTINGS_FILE=path.join(root,'settings.ini');app.disableHardwareAcceleration();
let confirmResponse=0,confirmCount=0;
dialog.showMessageBox=async(_win,options)=>{assert(options.message.includes('永久刪除'));assert(options.detail.includes(sources));confirmCount++;return {response:confirmResponse};};
const oldFile=path.join(sources,'old.txt'),recent=path.join(sources,'recent.txt'),xml=path.join(sources,'keep.xml');fs.writeFileSync(oldFile,'old source');fs.writeFileSync(recent,'recent source');fs.writeFileSync(xml,'<Root/>');
function age(file){assert(file.startsWith(sources+path.sep));execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',"$fixture=Get-Item -LiteralPath $env:DMT_SMOKE_OLD_TXT; $stamp=[DateTime]::UtcNow.AddDays(-31); $fixture.CreationTimeUtc=$stamp; $fixture.LastWriteTimeUtc=$stamp"],{env:{...process.env,DMT_SMOKE_OLD_TXT:file},windowsHide:true,stdio:'pipe'});}
age(oldFile);age(xml);require('./desktop');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const timeout=setTimeout(()=>{console.error('Cleanup smoke timed out');app.exit(1);},90000);timeout.unref();
app.whenReady().then(async()=>{try{
 let win;for(let n=0;n<150;n++){win=BrowserWindow.getAllWindows()[0];if(win&&!win.webContents.isLoading())break;await delay(100);}
 const js=code=>win.webContents.executeJavaScript(code);
 const wait=async condition=>{for(let n=0;n<200;n++){const state=await js('window.studio.cleanupStatus()');if(condition(state))return state;await delay(50);}throw new Error('Cleanup state timed out');};
 assert(!(await js('window.studio.cleanupStatus()')).config.enabled);
 await js("document.getElementById('language').value='zh-TW';document.getElementById('language').dispatchEvent(new Event('change'))");await delay(100);
 await js("document.getElementById('txt-cleanup').click()");for(let n=0;n<100;n++){if(await js("!!document.getElementById('cleanup-roots')"))break;await delay(50);}
 assert(await js("document.getElementById('txt-cleanup-panel').textContent.includes('永久刪除原始檔')"));
 const imported=await js('window.studio.cleanupRoots()');assert(new Set(imported.map(r=>r.mode)).size===3);
 await js(`document.getElementById('cleanup-roots').value=${JSON.stringify(sources)};document.getElementById('cleanup-roots').dispatchEvent(new Event('input'));document.getElementById('cleanup-preview').click()`);
 let state=await wait(s=>!s.busy&&s.preview?.report.count===1);assert(fs.existsSync(oldFile));assert.equal(state.preview.report.roots[0].errors.length,0);
 await js('window.studio.cleanupDelete()');assert(fs.existsSync(oldFile));assert.equal(confirmCount,1);
 confirmResponse=1;await js('window.studio.cleanupDelete()');state=await wait(s=>!s.busy&&s.latest?.deleted===1);
 assert(!fs.existsSync(oldFile));assert(fs.existsSync(recent)&&fs.existsSync(xml));assert(!state.config.enabled);assert(fs.existsSync(state.latest.log));
 const another=path.join(sources,'automatic.txt');fs.writeFileSync(another,'automatic old source');age(another);
 await js(`window.studio.cleanupPreview([${JSON.stringify(sources)}])`);await wait(s=>!s.busy&&s.preview?.report.count===1);
 await js(`window.studio.cleanupConfigure({enabled:true,roots:[${JSON.stringify(sources)}]})`);
 state=await wait(s=>!s.busy&&s.config.enabled&&s.config.nextAt>Date.now()+23*3600000&&!fs.existsSync(another));
 assert(!fs.existsSync(another));await js('window.studio.cleanupConfigure({enabled:false,roots:[]})');assert(!JSON.parse(fs.readFileSync(path.join(root,'txt-cleanup.json'))).enabled);
 await delay(1100);fs.writeFileSync(path.join(root,'cleanup-zh.png'),(await win.webContents.capturePage()).toPNG());
 console.log(JSON.stringify({defaultsOff:true,previewDoesNotDelete:true,cancelPreservesOriginal:true,onlyOldTxtDeleted:true,recentAndXmlPreserved:true,nativeConfirmationChinese:true,automaticIdleCleanup:true,policyCanDisable:true,auditSaved:true}));app.quit();
}catch(error){console.error(error);app.exit(1);}});
