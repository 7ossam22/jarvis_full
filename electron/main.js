const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path = require('path')
const os = require('os')

// Required for Linux sandbox
app.commandLine.appendSwitch('no-sandbox')
app.commandLine.appendSwitch('disable-setuid-sandbox')

let mainWindow

const isDev = process.env.NODE_ENV !== 'production' && !app.isPackaged

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
      allowRunningInsecureContent: true,
    },
    show: false,
    titleBarStyle: 'hidden'
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

app.whenReady().then(() => {
  // Grant all media permissions automatically
  const { session } = require('electron')
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    const allowed = ['media', 'microphone', 'camera', 'audioCapture', 'videoCapture', 'geolocation', 'notifications']
    callback(allowed.includes(permission))
  })
  session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
    const allowed = ['media', 'microphone', 'camera', 'audioCapture', 'videoCapture']
    return allowed.includes(permission)
  })

  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// Window control IPC handlers
ipcMain.on('window-minimize', () => {
  if (mainWindow) mainWindow.minimize()
})

ipcMain.on('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize()
    } else {
      mainWindow.maximize()
    }
  }
})

ipcMain.on('window-close', () => {
  if (mainWindow) mainWindow.close()
})

// System info IPC handlers
ipcMain.handle('get-system-info', () => {
  const totalMem = os.totalmem()
  const freeMem = os.freemem()
  const usedMem = totalMem - freeMem
  const ramPercent = Math.round((usedMem / totalMem) * 100)

  const cpus = os.cpus()
  let cpuLoad = 0
  if (cpus.length > 0) {
    const cpu = cpus[0]
    const total = Object.values(cpu.times).reduce((a, b) => a + b, 0)
    const idle = cpu.times.idle
    cpuLoad = Math.round(((total - idle) / total) * 100)
  }

  return {
    cpu: Math.max(15, Math.min(cpuLoad || Math.floor(Math.random() * 40 + 20), 85)),
    ram: ramPercent,
    totalRam: Math.round(totalMem / (1024 * 1024 * 1024)),
    usedRam: Math.round(usedMem / (1024 * 1024 * 1024)),
    platform: os.platform(),
    hostname: os.hostname(),
    uptime: Math.floor(os.uptime())
  }
})

ipcMain.handle('get-uptime', () => {
  return Math.floor(os.uptime())
})

// Open external applications
ipcMain.handle('open-spotify', async () => {
  try {
    const spotifyURI = 'spotify:'
    await shell.openExternal(spotifyURI)
    return { success: true }
  } catch (err) {
    try {
      await shell.openExternal('https://open.spotify.com')
      return { success: true, web: true }
    } catch (e) {
      return { success: false, error: e.message }
    }
  }
})

ipcMain.handle('open-url', async (event, url) => {
  try {
    await shell.openExternal(url)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('google-search', async (event, query) => {
  try {
    const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}`
    await shell.openExternal(searchUrl)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})
