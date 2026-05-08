const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path = require('path')
const os = require('os')
const http = require('http')
const https = require('https')
const { exec } = require('child_process')

// ── Chromium flags ────────────────────────────────────────────────────────────
// Enable Web Speech API (speech recognition) in Electron's Chromium
app.commandLine.appendSwitch('enable-features', 'WebSpeechAPI,SpeechSynthesis')
app.commandLine.appendSwitch('enable-speech-input')
app.commandLine.appendSwitch('allow-http-background-page')

// Linux: disable SUID sandbox (not needed or wanted on Windows/macOS)
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
    // mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  mainWindow.on('closed', () => { mainWindow = null })
}

app.whenReady().then(() => {
  const { session } = require('electron')

  // Grant all media permissions (mic, camera) automatically
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    const allowed = ['media', 'microphone', 'camera', 'audioCapture', 'videoCapture', 'geolocation', 'notifications', 'speech']
    callback(allowed.includes(permission))
  })
  session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
    return true // allow all permission checks
  })

  // Allow all media devices to be enumerated without permission prompt
  session.defaultSession.setDevicePermissionHandler(() => true)

  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// ── Window controls ───────────────────────────────────────────────────────────
ipcMain.on('window-minimize', () => { if (mainWindow) mainWindow.minimize() })
ipcMain.on('window-maximize', () => {
  if (!mainWindow) return
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize()
})
ipcMain.on('window-close', () => { if (mainWindow) mainWindow.close() })

// ── System info ───────────────────────────────────────────────────────────────
ipcMain.handle('get-system-info', () => {
  const totalMem = os.totalmem()
  const freeMem = os.freemem()
  const usedMem = totalMem - freeMem
  const cpus = os.cpus()
  let cpuLoad = 0
  if (cpus.length > 0) {
    const cpu = cpus[0]
    const total = Object.values(cpu.times).reduce((a, b) => a + b, 0)
    cpuLoad = Math.round(((total - cpu.times.idle) / total) * 100)
  }
  return {
    cpu: Math.max(15, Math.min(cpuLoad || Math.floor(Math.random() * 40 + 20), 85)),
    ram: Math.round((usedMem / totalMem) * 100),
    totalRam: Math.round(totalMem / (1024 ** 3)),
    usedRam: Math.round(usedMem / (1024 ** 3)),
    platform: os.platform(),
    hostname: os.hostname(),
    uptime: Math.floor(os.uptime()),
  }
})

ipcMain.handle('get-uptime', () => Math.floor(os.uptime()))

// ── Local AI proxy (bypasses CORS — runs in Node.js, not browser) ─────────────
function nodeRequest(baseUrl, urlPath, body, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    let parsed
    try { parsed = new URL(urlPath, baseUrl) } catch (e) { return reject(e) }

    const lib = parsed.protocol === 'https:' ? https : http
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      timeout: timeoutMs,
    }

    const req = lib.request(options, (res) => {
      let raw = ''
      res.on('data', c => { raw += c })
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(raw) }) }
        catch { resolve({ status: res.statusCode, data: raw }) }
      })
    })

    req.on('timeout', () => { req.destroy(new Error('Request timed out')) })
    req.on('error', reject)

    if (body) req.write(JSON.stringify(body))
    req.end()
  })
}

ipcMain.handle('local-ai-ping', async (event, { url }) => {
  try {
    const result = await nodeRequest(url, '/v1/models', null, 5000)
    const models = (result.data?.data || []).map(m => m.id)
    return { online: result.status < 400, models }
  } catch (err) {
    return { online: false, models: [], error: err.message }
  }
})

ipcMain.handle('local-ai-chat', async (event, { url, body }) => {
  try {
    const result = await nodeRequest(url, '/v1/chat/completions', body, 60000)
    if (result.status >= 400) {
      const msg = result.data?.error?.message || `HTTP ${result.status}`
      return { ok: false, error: msg }
    }
    const text = result.data?.choices?.[0]?.message?.content
    return { ok: true, text: text || '' }
  } catch (err) {
    return { ok: false, error: err.message }
  }
})

// ── Spotify ───────────────────────────────────────────────────────────────────
ipcMain.handle('open-spotify', async () => {
  const platform = os.platform()
  const tryUri = (uri) => shell.openExternal(uri).then(() => true).catch(() => false)
  const tryExec = (cmd) => new Promise(r => exec(cmd, { timeout: 5000 }, err => r(!err)))

  if (platform === 'win32') {
    if (await tryUri('spotify:')) return { success: true }
    const storePath = path.join(process.env.LOCALAPPDATA || '', 'Microsoft\\WindowsApps\\Spotify.exe')
    if (await tryExec(`"${storePath}"`)) return { success: true }
    const appPath = path.join(process.env.APPDATA || '', 'Spotify\\Spotify.exe')
    if (await tryExec(`"${appPath}"`)) return { success: true }
  }
  if (platform === 'darwin') {
    if (await tryUri('spotify:')) return { success: true }
    if (await tryExec('open -a Spotify')) return { success: true }
  }
  if (platform === 'linux') {
    if (await tryUri('spotify:')) return { success: true }
    if (await tryExec('spotify')) return { success: true }
    if (await tryExec('flatpak run com.spotify.Client')) return { success: true }
    if (await tryExec('snap run spotify')) return { success: true }
  }

  if (await tryUri('https://open.spotify.com')) return { success: true, web: true }
  return { success: false, error: 'Spotify not found on this system.' }
})

ipcMain.handle('open-url', async (event, url) => {
  try { await shell.openExternal(url); return { success: true } }
  catch (err) { return { success: false, error: err.message } }
})

ipcMain.handle('google-search', async (event, query) => {
  try {
    await shell.openExternal(`https://www.google.com/search?q=${encodeURIComponent(query)}`)
    return { success: true }
  } catch (err) { return { success: false, error: err.message } }
})
