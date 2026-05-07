const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('astudioDesktop', {
  getAppInfo: () => ipcRenderer.invoke('app:get-info'),
  openExternal: (url) => ipcRenderer.invoke('app:open-external', url),
  getUpdateState: () => ipcRenderer.invoke('app:get-update-state'),
  checkForUpdates: () => ipcRenderer.invoke('app:check-for-updates'),
  downloadUpdate: () => ipcRenderer.invoke('app:download-update'),
  installUpdate: () => ipcRenderer.invoke('app:install-update'),
  onUpdateState: (callback) => {
    const listener = (_event, state) => callback(state)
    ipcRenderer.on('app:update-state', listener)
    return () => ipcRenderer.removeListener('app:update-state', listener)
  },
})
