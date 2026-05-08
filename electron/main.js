const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path = require('path')
const os = require('os')
const { exec } = require('child_process')

// Linux requires disabling the SUID sandbox (not needed or wanted on Windows/macOS)
if (process.platform === 'linux') {
  app.commandLine.appendSwitch('no-sandbox')
  app.commandLine.appendSwitch('disable-setuid-sandbox')
}

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
  const platform = os.platform()

  // Helper: try shell.openExternal and resolve with result
  const tryUri = (uri) => new Promise(resolve => {
    shell.openExternal(uri).then(() => resolve(true)).catch(() => resolve(false))
  })

  // Helper: try running a command silently
  const tryExec = (cmd) => new Promise(resolve => {
    exec(cmd, { timeout: 5000 }, (err) => resolve(!err))
  })

  // ── Windows ──────────────────────────────────────────────────────────────
  if (platform === 'win32') {
    // 1. Spotify URI scheme (works if Spotify from any source is installed)
    if (await tryUri('spotify:')) return { success: true }

    // 2. Windows Store / UWP path
    const winStorePath = path.join(
      process.env.LOCALAPPDATA || '',
      'Microsoft\\WindowsApps\\Spotify.exe'
    )
    if (await tryExec(`"${winStorePath}"`)) return { success: true }

    // 3. Traditional install path
    const winPath = path.join(
      process.env.APPDATA || '',
      'Spotify\\Spotify.exe'
    )
    if (await tryExec(`"${winPath}"`)) return { success: true }
  }

  // ── macOS ─────────────────────────────────────────────────────────────────
  if (platform === 'darwin') {
    if (await tryUri('spotify:')) return { success: true }
    if (await tryExec('open -a Spotify')) return { success: true }
  }

  // ── Linux ─────────────────────────────────────────────────────────────────
  if (platform === 'linux') {
    // Try URI first (works if xdg-open knows the handler)
    if (await tryUri('spotify:')) return { success: true }
    // Native .deb / .rpm install
    if (await tryExec('spotify')) return { success: true }
    // Flatpak
    if (await tryExec('flatpak run com.spotify.Client')) return { success: true }
    // Snap
    if (await tryExec('snap run spotify')) return { success: true }
  }

  // ── Final fallback: web player ─────────────────────────────────────────────
  if (await tryUri('https://open.spotify.com')) return { success: true, web: true }

  return { success: false, error: 'Spotify could not be found on this system.' }
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
