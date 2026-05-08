const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // Window controls
  minimizeWindow: () => ipcRenderer.send('window-minimize'),
  maximizeWindow: () => ipcRenderer.send('window-maximize'),
  closeWindow: () => ipcRenderer.send('window-close'),

  // System info
  getSystemInfo: () => ipcRenderer.invoke('get-system-info'),
  getUptime: () => ipcRenderer.invoke('get-uptime'),

  // App launching
  openSpotify: () => ipcRenderer.invoke('open-spotify'),
  openUrl: (url) => ipcRenderer.invoke('open-url', url),
  googleSearch: (query) => ipcRenderer.invoke('google-search', query),
})
