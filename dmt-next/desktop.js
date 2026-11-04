const {app,BrowserWindow,ipcMain,dialog,shell} = require('electron');
const fs=require('node:fs/promises');const path=require('node:path');
const {GuiClient}=require('./gui-client');const {installUpdater}=require('./updater');
let win,gui;
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
    gui.close();gui.start();return true;
  });
  ipcMain.handle('ui:language',(_e,value)=>{language=value==='zh-TW'?'zh-TW':'ko';});
  ipcMain.handle('gui:snapshot',()=>gui.call('gui.snapshot'));
  ipcMain.handle('gui:action',(_e,args)=>gui.call('gui.action',args));
  ipcMain.handle('gui:reply',(_e,args)=>gui.call('dialog.reply',args));
  ipcMain.handle('settings:folder',()=>shell.openPath(app.getPath('userData')));
  ipcMain.handle('settings:import',async()=>{
    if(gui.busy||gui.pending.size)throw new Error('작업 완료 후 가져오세요. / 請先完成工作。');
    const r=await dialog.showOpenDialog(win,{properties:['openFile'],filters:[{name:'Model settings',extensions:['json','txt']}]});if(r.canceled)return null;
    const source=await fs.readFile(r.filePaths[0],'utf8');const data=JSON.parse(source.replace(/^\uFEFF/,''));
    if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('Invalid model settings');
    const dest=path.join(app.getPath('userData'),'yield_model_settings.json');
    try{await fs.copyFile(dest,dest+'.bak');}catch(e){if(e.code!=='ENOENT')throw e;}
    await fs.writeFile(dest+'.tmp',JSON.stringify(data,null,2));await fs.rename(dest+'.tmp',dest);
    await gui.call('gui.reload-models');return Object.keys(data).length;
  });
  installUpdater(app,ipcMain,()=>gui.busy||gui.pending.size>0);
  win=new BrowserWindow({width:1500,height:1000,minWidth:900,minHeight:650,backgroundColor:'#f3f6fb',title:'DMT Analysis Studio',webPreferences:{preload:path.join(__dirname,'desktop-preload.js'),contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:!app.isPackaged&&process.env.DMT_HEADLESS==='1'}});
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  win.on('close',event=>{if(gui.busy||gui.pending.size){event.preventDefault();dialog.showMessageBoxSync(win,{type:'info',message:'진행 중인 작업을 완료하거나 중지하세요. / 請先完成或停止工作。'});}});
  win.webContents.on('will-navigate',event=>event.preventDefault());
  await win.loadFile(path.join(__dirname,'ui','studio.html'));
  app.on('before-quit',event=>{if(gui.busy||gui.pending.size){event.preventDefault();dialog.showMessageBoxSync(win,{type:'info',message:'진행 중인 작업을 완료하거나 중지하세요. / 請先完成或停止工作。'});return;}gui.close();});
});
app.on('window-all-closed',()=>app.quit());
