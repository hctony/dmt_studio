const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('dmt', {
  openOriginal: () => ipcRenderer.invoke('legacy:open'),
  chooseFiles: () => ipcRenderer.invoke('file:choose'),
  saveResult: value => ipcRenderer.invoke('result:save', value),
  updateStatus: () => ipcRenderer.invoke('update:status'),
  checkUpdate: () => ipcRenderer.invoke('update:check'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  cacheStatus: () => ipcRenderer.invoke('legacy:cache-status'),
  clearCache: () => ipcRenderer.invoke('legacy:cache-clear'),
  legacySettings: () => ipcRenderer.invoke('legacy:settings'),
  analyze: (method, args) => ipcRenderer.invoke('legacy:analyze', method, args),
  load: () => ipcRenderer.invoke('profiles:load'),
  save: state => ipcRenderer.invoke('profiles:save', state),
  chooseDirectory: () => ipcRenderer.invoke('directory:choose'),
  importModelSettings: () => ipcRenderer.invoke('model-settings:import')
});
