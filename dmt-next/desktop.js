const {app,BrowserWindow,ipcMain,dialog,shell} = require('electron');
const fs=require('node:fs/promises');const path=require('node:path');
const {GuiClient}=require('./gui-client');const {installUpdater}=require('./updater');
const {installLifecycle}=require('./lifecycle');
let win,gui,lifecycle;
const primaryInstance=app.requestSingleInstanceLock();
if(!primaryInstance)app.quit();
app.on('second-instance',()=>lifecycle?.show());
let language='ko';const translations=require('./ui/translations.json');
let recentPaths={};
function translate(s){if(language==='ko')return s;let value=translations[s]||s;for(const key of Object.keys(translations).filter(k=>k.length>3).sort((a,b)=>b.length-a.length)){value=value.split(key).join(translations[key]);}return value;}
async function onEvent(m){
  if(m.event==='dialog' && ['file','files','save','directory'].includes(m.dialog.kind)){
    const d={...m.dialog,title:translate(m.dialog.title)};let value;
    const remembered=recentPaths[m.dialog.title]||recentPaths[d.kind];
    const defaultPath=d.path?(path.isAbsolute(d.path)?d.path:remembered?path.join(remembered,d.path):d.path):remembered;
    const filters=(d.filter||'').split(';;').map(part=>{const match=part.match(/^(.*?)\((.*?)\)$/);if(!match)return null;const extensions=[...match[2].matchAll(/\*\.([^\s;)]+)/g)].map(m=>m[1]);return extensions.length?{name:match[1].trim(),extensions}:null;}).filter(Boolean);
    try{
      if(d.kind==='save'){const r=await dialog.showSaveDialog(win,{title:d.title,defaultPath,filters:filters.length?filters:undefined});value=r.canceled?'':r.filePath;}
      else{const r=await dialog.showOpenDialog(win,{title:d.title,defaultPath,filters:filters.length?filters:undefined,properties:d.kind==='directory'?['openDirectory']:d.kind==='files'?['openFile','multiSelections']:['openFile']});value=r.canceled?(d.kind==='files'?[]:''):(d.kind==='files'?r.filePaths:r.filePaths[0]);}
      const chosen=Array.isArray(value)?value[0]:value;
      if(chosen){const folder=d.kind==='directory'?chosen:path.dirname(chosen);recentPaths[m.dialog.title]=folder;recentPaths[d.kind]=folder;await fs.writeFile(path.join(app.getPath('userData'),'dialog-paths.json'),JSON.stringify(recentPaths));}
    }catch{value=d.kind==='files'?[]:'';}
    gui.call('dialog.reply',{id:d.id,value}).catch(()=>{});
  }else if(win&&!win.isDestroyed())win.webContents.send('gui:event',m);
}
app.whenReady().then(async()=>{
  if(!primaryInstance)return;
  await fs.mkdir(app.getPath('userData'),{recursive:true});
  try{recentPaths=JSON.parse(await fs.readFile(path.join(app.getPath('userData'),'dialog-paths.json'),'utf8'));}catch{}
  gui=new GuiClient(onEvent);
  const connectionKeys=['DMT_NAS_HOST','DMT_NAS_PORT','DMT_NAS_USER','DMT_NAS_PASSWORD','DMT_NAS_ROOT'];
  async function readConnection(){const result={};try{const content=await fs.readFile(path.join(app.getPath('userData'),'.env'),'utf8');for(const line of content.split(/\r?\n/)){const i=line.indexOf('=');if(i>0&&connectionKeys.includes(line.slice(0,i).trim()))result[line.slice(0,i).trim()]=line.slice(i+1).trim();}}catch(e){if(e.code!=='ENOENT')throw e;}return result;}
  ipcMain.handle('settings:connection-read',async()=>{const values=await readConnection();const hasPassword=!!values.DMT_NAS_PASSWORD;delete values.DMT_NAS_PASSWORD;return {values,hasPassword};});
  ipcMain.handle('settings:connection-save',async(_e,values)=>{
    if(gui.busy||gui.pending.size)throw new Error('작업 완료 후 저장하세요. / 請先完成工作。');
    const current=await readConnection();
    for(const key of connectionKeys){if(typeof values[key]!=='string'||/[\r\n]/.test(values[key]))throw new Error('Invalid connection value');if(key==='DMT_NAS_PASSWORD'&&!values[key])continue;current[key]=values[key];}
    const port=Number(current.DMT_NAS_PORT);if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Invalid NAS port');
    const dest=path.join(app.getPath('userData'),'.env');let other='';try{other=(await fs.readFile(dest,'utf8')).split(/\r?\n/).filter(line=>!connectionKeys.includes(line.split('=')[0].trim())).join('\n');}catch(e){if(e.code!=='ENOENT')throw e;}
    await fs.writeFile(dest+'.tmp',other+'\n'+connectionKeys.map(key=>`${key}=${current[key]||''}`).join('\n'));await fs.rename(dest+'.tmp',dest);
    await gui.retry();return true;
  });
  ipcMain.handle('ui:language',(_e,value)=>{language=value==='zh-TW'?'zh-TW':'ko';lifecycle?.setLanguage(language);});
  ipcMain.handle('gui:snapshot',()=>gui.call('gui.snapshot'));
  ipcMain.handle('gui:image-step',(_e,direction)=>gui.call('gui.image-step',{direction:direction===-1?-1:1}));
  ipcMain.handle('backend:retry',()=>{if(gui.busy||gui.pending.size||gui.updating)throw new Error('BACKEND_BUSY');return gui.retry();});
  ipcMain.handle('backend:status',()=>({error:gui.failure?.message||null}));
  ipcMain.handle('gui:action',(_e,args)=>gui.call('gui.action',args));
  ipcMain.handle('gui:reply',(_e,args)=>gui.call('dialog.reply',args));
  ipcMain.handle('workflow:call',(_e,method,args={})=>{
    if(!['status','start','finish','recommend','apply','preferences'].includes(method))throw new Error('Invalid workflow method');
    if(['start','finish','apply'].includes(method)&&(gui.busy||gui.pending.size))throw new Error('작업 완료 후 사용하세요. / 請先完成工作。');
    return gui.call('workflow.'+method,{...args,version:app.getVersion()});
  });
  ipcMain.handle('workflow:export',async()=>{
    const data=await gui.call('workflow.export');
    const r=await dialog.showSaveDialog(win,{title:'LOT 로그 내보내기 / 匯出 LOT 紀錄',defaultPath:'dmt-lot-workflows.json',filters:[{name:'JSON',extensions:['json']}]});
    if(r.canceled)return null;
    await fs.writeFile(r.filePath,JSON.stringify(data,null,2),'utf8');return r.filePath;
  });
  ipcMain.handle('settings:folder',()=>shell.openPath(app.getPath('userData')));
  ipcMain.handle('settings:models',()=>gui.call('gui.models'));
  ipcMain.handle('settings:apply-model',(_e,args)=>{
    if(gui.busy||gui.pending.size)throw new Error('작업 완료 후 사용하세요. / 請先完成工作。');
    return gui.call('gui.apply-model',{model:args.model,gathering:args.gathering===true});
  });
  ipcMain.handle('settings:import',async()=>{
    if(gui.busy||gui.pending.size)throw new Error('작업 완료 후 가져오세요. / 請先完成工作。');
    const r=await dialog.showOpenDialog(win,{properties:['openFile'],filters:[{name:'Model settings',extensions:['json','txt']}]});if(r.canceled)return null;
    if((await fs.stat(r.filePaths[0])).size>5*1024*1024)throw new Error('설정 파일이 너무 큽니다. / 設定檔過大。');
    const source=await fs.readFile(r.filePaths[0],'utf8');let data;
    try{data=JSON.parse(source.replace(/^\uFEFF/,''));}catch{throw new Error('올바른 JSON 파일을 선택하세요. / 請選擇有效的 JSON 檔案。');}
    return gui.call('gui.import-models',{data});
  });
  installUpdater(app,ipcMain,()=>gui.busy||gui.pending.size>0,{
    onState:state=>{if(win&&!win.isDestroyed())win.webContents.send('update:state',state);},
    beforeInstall:()=>gui.stopForUpdate(),
    onInstallError:()=>{gui.updating=false;}
  });
  win=new BrowserWindow({width:1500,height:1000,minWidth:900,minHeight:650,backgroundColor:'#f3f6fb',title:'DMT Analysis Studio',webPreferences:{preload:path.join(__dirname,'desktop-preload.js'),contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:!app.isPackaged&&process.env.DMT_HEADLESS==='1'}});
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  lifecycle=installLifecycle({app,win,gui,iconPath:path.join(__dirname,'assets','tray.ico')});
  win.webContents.on('will-navigate',event=>event.preventDefault());
  await win.loadFile(path.join(__dirname,'ui','studio.html'));
});
app.on('window-all-closed',()=>app.quit());
