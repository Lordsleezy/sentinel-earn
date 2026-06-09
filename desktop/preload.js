const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sentinelEarn', {
  getApiBase: () => ipcRenderer.invoke('get-api-base'),
  getUpdateStatus: () => ipcRenderer.invoke('earn:getUpdateStatus'),
  restartToUpdate: () => ipcRenderer.invoke('earn:restartToUpdate'),
  onUpdate: (callback) => {
    const handler = (_event, status) => callback(status);
    ipcRenderer.on('earn:update', handler);
    return () => ipcRenderer.removeListener('earn:update', handler);
  },
});
