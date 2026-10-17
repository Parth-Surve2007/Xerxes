const { contextBridge, ipcRenderer } = require('electron');

const port = new URLSearchParams(location.search).get('apiPort');
contextBridge.exposeInMainWorld('xerxes', Object.freeze({
  apiBase: port ? `http://127.0.0.1:${port}/api/v1` : null,
  desktop: true
  ,selectFolder: () => ipcRenderer.invoke('select-folder')
  ,selectWorkspace: () => ipcRenderer.invoke('select-workspace')
  ,revokeFolder: grantId => ipcRenderer.invoke('revoke-folder', grantId)
}));
