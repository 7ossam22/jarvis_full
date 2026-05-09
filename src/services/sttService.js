// Local speech-to-text using Whisper (runs entirely offline, no external URLs)
// Model files must be present in public/models/ — run: node scripts/download-models.js
import { pipeline, env } from '@xenova/transformers'

// Single-threaded WASM — no SharedArrayBuffer / COOP+COEP headers needed
env.backends.onnx.wasm.numThreads = 1
// WASM files are in dist/node_modules/onnxruntime-web/dist/ (copied by viteStaticCopy)
// In dev Vite serves node_modules at /node_modules/ via server.fs.allow
env.backends.onnx.wasm.wasmPaths = `${window.location.origin}/node_modules/onnxruntime-web/dist/`

// Use only locally-bundled model files — never contact HuggingFace
env.allowRemoteModels = false
env.allowLocalModels  = true
env.localModelPath    = `${window.location.origin}/models/`

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
