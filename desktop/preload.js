const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sentinelEarn', {
  getApiBase: () => ipcRenderer.invoke('get-api-base'),
  getBackendStatus: () => ipcRenderer.invoke('earn:getBackendStatus'),
  openPythonDownload: () => ipcRenderer.invoke('earn:openPythonDownload'),
  openExternal: (url) => ipcRenderer.invoke('earn:openExternal', url),
  onBackendStatus: (callback) => {
    const handler = (_event, status) => callback(status);
    ipcRenderer.on('earn:backend-status', handler);
    return () => ipcRenderer.removeListener('earn:backend-status', handler);
  },
  getUpdateStatus: () => ipcRenderer.invoke('earn:getUpdateStatus'),
  restartToUpdate: () => ipcRenderer.invoke('earn:restartToUpdate'),
  onUpdate: (callback) => {
    const handler = (_event, status) => callback(status);
    ipcRenderer.on('earn:update', handler);
    return () => ipcRenderer.removeListener('earn:update', handler);
  },
});
