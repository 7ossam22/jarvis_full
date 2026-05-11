import { pipeline, env } from '@xenova/transformers'
import { logger } from './logger'

// Use bundled local models and local WASM — fully offline
env.allowRemoteModels = false
env.localModelPath = '/models/'
env.backends.onnx.wasm.numThreads = 1
env.backends.onnx.wasm.wasmPaths = '/ort/'

let transcriber = null
let loadPromise = null

export async function loadWhisper(onStatus) {
  if (transcriber) return transcriber
  if (loadPromise) return loadPromise

  loadPromise = (async () => {
    try {
      logger.info('WHISPER', 'Loading local Whisper model...')
      onStatus?.('loading')
      transcriber = await pipeline(
        'automatic-speech-recognition',
        'Xenova/whisper-tiny.en',
        { local_files_only: true }
      )
      logger.info('WHISPER', 'Whisper model ready')
      onStatus?.('ready')
      return transcriber
    } catch (err) {
      loadPromise = null
      logger.error('WHISPER', `Model load failed: ${err.message}`)
      onStatus?.('error')
      throw err
    }
  })()

  return loadPromise
}

export async function transcribeFloat32(float32Audio) {
  if (!transcriber) throw new Error('Whisper model not loaded')
  logger.debug('WHISPER', `Transcribing ${float32Audio.length} samples at 16kHz`)
  const result = await transcriber(float32Audio, {
    sampling_rate: 16000,
    return_timestamps: false,
    language: 'english',
    task: 'transcribe',
  })
  const text = result.text.trim()
  logger.info('WHISPER', `Result: "${text}"`)
  return text
}

// Decode a Blob of audio (webm/opus etc.) into a 16kHz mono Float32Array
export async function blobToFloat32(blob) {
  const arrayBuffer = await blob.arrayBuffer()
  // Create an AudioContext at 16kHz so decodeAudioData resamples for us
  const ctx = new AudioContext({ sampleRate: 16000 })
  try {
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer)
    return audioBuffer.getChannelData(0) // mono channel
  } finally {
    ctx.close()
  }
}
