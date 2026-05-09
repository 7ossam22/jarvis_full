// Local speech-to-text using Whisper (runs entirely in-browser, no Google)
import { pipeline, env } from '@xenova/transformers'

// Single-threaded WASM — avoids SharedArrayBuffer requirement (no COOP/COEP headers needed)
env.backends.onnx.wasm.numThreads = 1

// Point ONNX Runtime to the WASM files served by the local HTTP server.
// In dev: Vite serves node_modules at /node_modules/ (server.fs.allow: ['..'])
// In prod: viteStaticCopy copies them to dist/node_modules/onnxruntime-web/dist/
env.backends.onnx.wasm.wasmPaths = `${window.location.origin}/node_modules/onnxruntime-web/dist/`

env.useBrowserCache = true
env.allowLocalModels = false

// Monkey-patch window.fetch to route HuggingFace requests through Electron IPC.
// The Electron main process uses Node.js https (no CORS, no firewall) and caches
// downloaded files to disk. The renderer caches them in the Cache API for fast
// subsequent loads without touching the network at all.
if (window.electronAPI?.fetchHfFile) {
  const _origFetch = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input
      : input instanceof Request ? input.url
      : String(input)

    const isHF = url.includes('huggingface.co') || url.includes('cdn-lfs.huggingface.co')
    if (!isHF) return _origFetch(input, init)

    // Fast path: already in browser cache from a previous run
    try {
      const cache = await caches.open('hf-models-v1')
      const hit = await cache.match(url)
      if (hit) return hit
    } catch {}

    // Download via Electron main process (Node.js — no CORS, no firewall issues)
    const result = await window.electronAPI.fetchHfFile(url)
    if (!result.ok) throw new TypeError(`Failed to fetch ${url}: ${result.error}`)

    // base64 → Uint8Array
    const binary = atob(result.base64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)

    const response = new Response(bytes, {
      status: 200,
      headers: { 'Content-Type': result.contentType || 'application/octet-stream' },
    })

    // Store in Cache API so the next app launch skips IPC entirely
    try {
      const cache = await caches.open('hf-models-v1')
      await cache.put(url, response.clone())
    } catch {}

    return response
  }
}

let transcriber = null
let loadPromise = null

export async function loadModel(onProgress) {
  if (transcriber) return
  if (loadPromise) { await loadPromise; return }

  loadPromise = pipeline(
    'automatic-speech-recognition',
    'Xenova/whisper-tiny.en',
    { progress_callback: onProgress }
  ).then(pipe => {
    transcriber = pipe
    loadPromise = null
  }).catch(err => {
    loadPromise = null
    throw err
  })

  await loadPromise
}

export const isLoaded = () => !!transcriber

// Accepts a Blob (audio/webm from MediaRecorder), returns transcript string
export async function transcribeBlob(blob) {
  if (!transcriber) throw new Error('STT model not loaded')

  const arrayBuffer = await blob.arrayBuffer()

  const tmpCtx = new AudioContext()
  let decoded
  try {
    decoded = await tmpCtx.decodeAudioData(arrayBuffer)
  } finally {
    tmpCtx.close()
  }

  // Whisper needs 16 kHz mono Float32Array
  let float32
  if (decoded.sampleRate === 16000 && decoded.numberOfChannels === 1) {
    float32 = decoded.getChannelData(0)
  } else {
    const len = Math.ceil(decoded.duration * 16000)
    const off = new OfflineAudioContext(1, Math.max(len, 1), 16000)
    const src = off.createBufferSource()
    src.buffer = decoded
    src.connect(off.destination)
    src.start(0)
    const resampled = await off.startRendering()
    float32 = resampled.getChannelData(0)
  }

  const result = await transcriber(float32, {
    language: 'english',
    task: 'transcribe',
    return_timestamps: false,
  })

  return (result.text || '').trim()
}
