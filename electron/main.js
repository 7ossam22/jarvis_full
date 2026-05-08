const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path = require('path')
const os = require('os')
const http = require('http')
const https = require('https')
const fs = require('fs')
const { exec } = require('child_process')

// Linux: disable SUID sandbox
if (process.platform === 'linux') {
  app.commandLine.appendSwitch('no-sandbox')
  app.commandLine.appendSwitch('disable-setuid-sandbox')
}

let mainWindow
let localServer = null

const isDev = process.env.NODE_ENV !== 'production' && !app.isPackaged

// ── Production static file server ─────────────────────────────────────────────
// Serves dist/ over http://127.0.0.1 so Web Speech API works (blocked on file://)
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.png':  'image/png',
  '.ico':  'image/x-icon',
  '.svg':  'image/svg+xml',
  '.json': 'application/json',
  '.woff': 'font/woff',
  '.woff2':'font/woff2',
  '.ttf':  'font/ttf',
}

function startLocalServer(distPath) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const urlPath = req.url.split('?')[0]
      let filePath = path.join(distPath, urlPath === '/' ? 'index.html' : urlPath)

      if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        filePath = path.join(distPath, 'index.html')
      }

      fs.readFile(filePath, (err, data) => {
        if (err) { res.writeHead(404); res.end('Not found'); return }
        const ext = path.extname(filePath)
        res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' })
        res.end(data)
      })
    })

    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      resolve({ server, port })
    })
  })
}

// ── Window ────────────────────────────────────────────────────────────────────
async function createWindow() {
  let appUrl

  if (isDev) {
    appUrl = 'http://localhost:5173'
  } else {
    const distPath = path.join(__dirname, '../dist')
    const { server, port } = await startLocalServer(distPath)
    localServer = server
    appUrl = `http://127.0.0.1:${port}`
  }

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
    },
    show: false,
    titleBarStyle: 'hidden',
  })

  mainWindow.loadURL(appUrl)

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
    if (localServer) { localServer.close(); localServer = null }
  })
}

app.whenReady().then(() => {
  const { session } = require('electron')

  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    const allowed = ['media', 'microphone', 'camera', 'audioCapture', 'videoCapture', 'geolocation', 'notifications']
    callback(allowed.includes(permission))
  })

  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (localServer) localServer.close()
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

// ── Generic Node.js HTTP proxy (no CORS) ─────────────────────────────────────
function nodeRequest(baseUrl, urlPath, body, headers = {}, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    let parsed
    try { parsed = new URL(urlPath, baseUrl) } catch (e) { return reject(e) }

    const lib = parsed.protocol === 'https:' ? https : http
    const bodyStr = body ? JSON.stringify(body) : null
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json', ...headers },
      timeout: timeoutMs,
    }
    if (bodyStr) options.headers['Content-Length'] = Buffer.byteLength(bodyStr)

    const chunks = []
    const req = lib.request(options, (res) => {
      res.on('data', c => chunks.push(c))
      res.on('end', () => {
        const buf = Buffer.concat(chunks)
        const ct = res.headers['content-type'] || ''
        if (ct.includes('application/json')) {
          try { resolve({ status: res.statusCode, data: JSON.parse(buf.toString()), binary: false }) }
          catch { resolve({ status: res.statusCode, data: buf.toString(), binary: false }) }
        } else {
          resolve({ status: res.statusCode, data: buf.toString('base64'), binary: true })
        }
      })
    })

    req.on('timeout', () => req.destroy(new Error('Request timed out')))
    req.on('error', reject)
    if (bodyStr) req.write(bodyStr)
    req.end()
  })
}

// Local AI
ipcMain.handle('local-ai-ping', async (event, { url }) => {
  try {
    const result = await nodeRequest(url, '/v1/models', null, {}, 5000)
    const models = (result.data?.data || []).map(m => m.id)
    return { online: result.status < 400, models }
  } catch (err) {
    return { online: false, models: [], error: err.message }
  }
})

ipcMain.handle('local-ai-chat', async (event, { url, body }) => {
  try {
    const result = await nodeRequest(url, '/v1/chat/completions', body, {}, 60000)
    if (result.status >= 400) return { ok: false, error: result.data?.error?.message || `HTTP ${result.status}` }
    return { ok: true, text: result.data?.choices?.[0]?.message?.content || '' }
  } catch (err) {
    return { ok: false, error: err.message }
  }
})

// ElevenLabs TTS — returns base64 audio/mpeg
ipcMain.handle('elevenlabs-tts', async (event, { apiKey, voiceId, text, modelId }) => {
  try {
    const result = await nodeRequest(
      'https://api.elevenlabs.io',
      `/v1/text-to-speech/${voiceId}`,
      { text, model_id: modelId || 'eleven_turbo_v2_5', voice_settings: { stability: 0.55, similarity_boost: 0.80, style: 0.2, use_speaker_boost: true } },
      { 'xi-api-key': apiKey, Accept: 'audio/mpeg' },
      30000
    )
    if (result.status >= 400) return { ok: false, error: `ElevenLabs HTTP ${result.status}` }
    return { ok: true, audio: result.data } // base64 mp3
  } catch (err) {
    return { ok: false, error: err.message }
  }
})

// ── Spotify ───────────────────────────────────────────────────────────────────
ipcMain.handle('open-spotify', async () => {
  const platform = os.platform()
  const tryUri  = (uri) => shell.openExternal(uri).then(() => true).catch(() => false)
  const tryExec = (cmd) => new Promise(r => exec(cmd, { timeout: 5000 }, err => r(!err)))

  if (platform === 'win32') {
    if (await tryUri('spotify:')) return { success: true }
    const store = path.join(process.env.LOCALAPPDATA || '', 'Microsoft\\WindowsApps\\Spotify.exe')
    if (await tryExec(`"${store}"`)) return { success: true }
    const app2 = path.join(process.env.APPDATA || '', 'Spotify\\Spotify.exe')
    if (await tryExec(`"${app2}"`)) return { success: true }
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
  return { success: false, error: 'Spotify not found.' }
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
