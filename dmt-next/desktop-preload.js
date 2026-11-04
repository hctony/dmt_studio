const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('studio',{
  workflow:(method,args)=>ipcRenderer.invoke('workflow:call',method,args),
  exportWorkflows:()=>ipcRenderer.invoke('workflow:export'),
  language:value=>ipcRenderer.invoke('ui:language',value),
  connection:()=>ipcRenderer.invoke('settings:connection-read'),
  saveConnection:values=>ipcRenderer.invoke('settings:connection-save',values),
  snapshot:()=>ipcRenderer.invoke('gui:snapshot'),
  action:args=>ipcRenderer.invoke('gui:action',args),
  reply:args=>ipcRenderer.invoke('gui:reply',args),
  onEvent:callback=>{const handler=(_e,value)=>callback(value);ipcRenderer.on('gui:event',handler);return()=>ipcRenderer.removeListener('gui:event',handler);},
  importSettings:()=>ipcRenderer.invoke('settings:import'),
  settingsFolder:()=>ipcRenderer.invoke('settings:folder'),
  updateStatus:()=>ipcRenderer.invoke('update:status'),
  checkUpdate:()=>ipcRenderer.invoke('update:check'),
  installUpdate:()=>ipcRenderer.invoke('update:install')
});
