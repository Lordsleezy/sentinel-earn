const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sentinelEarn', {
  getApiBase: () => ipcRenderer.invoke('get-api-base'),
});
