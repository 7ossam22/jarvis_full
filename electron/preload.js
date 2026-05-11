const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // Window controls
  minimizeWindow: () => ipcRenderer.send('window-minimize'),
  maximizeWindow: () => ipcRenderer.send('window-maximize'),
  closeWindow:    () => ipcRenderer.send('window-close'),

  // System info
  getSystemInfo: () => ipcRenderer.invoke('get-system-info'),
  getUptime:     () => ipcRenderer.invoke('get-uptime'),

  // Local AI — routed through main process to avoid CORS
  localAiPing: (url)        => ipcRenderer.invoke('local-ai-ping', { url }),
  localAiChat: (url, body)  => ipcRenderer.invoke('local-ai-chat', { url, body }),

  // ElevenLabs TTS — returns base64 audio/mpeg
  elevenLabsTts: (params) => ipcRenderer.invoke('elevenlabs-tts', params),

  // Spotify media controls
  spotifyControl: (params) => ipcRenderer.invoke('spotify-control', params),

  // HuggingFace model download proxy (routes through Node.js to bypass CORS/firewall)
  fetchHfFile: (url) => ipcRenderer.invoke('fetch-hf-file', url),

  // App launching
  openSpotify:  ()      => ipcRenderer.invoke('open-spotify'),
  closeSpotify: ()      => ipcRenderer.invoke('close-spotify'),
  openUrl:      (url)   => ipcRenderer.invoke('open-url', url),
  googleSearch: (query) => ipcRenderer.invoke('google-search', query),
})
