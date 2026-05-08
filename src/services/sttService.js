// Local speech-to-text using Whisper (runs entirely in-browser, no Google)
import { pipeline, env } from '@xenova/transformers'

env.useBrowserCache = true   // cache model after first download
env.allowLocalModels = false

let transcriber = null
let loadPromise = null

// progress_callback receives { status, progress, file, ... }
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

  // Decode to PCM
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
