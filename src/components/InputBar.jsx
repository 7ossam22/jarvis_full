import React, { useRef, useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { logger } from '../services/logger'
import { loadWhisper, transcribeFloat32, blobToFloat32 } from '../services/whisperService'

// Silence detection thresholds
const VOLUME_THRESHOLD = 12       // 0–255 scale; below = silence
const SPEECH_MIN_MS    = 400      // ignore blips shorter than this
const SILENCE_AFTER_MS = 1400     // stop recording after this much silence post-speech

export default function InputBar({ value, onChange, onSend, isProcessing }) {
  const isProcessingRef = useRef(isProcessing)
  const onSendRef       = useRef(onSend)
  const onChangeRef     = useRef(onChange)
  useEffect(() => { isProcessingRef.current = isProcessing }, [isProcessing])
  useEffect(() => { onSendRef.current = onSend },             [onSend])
  useEffect(() => { onChangeRef.current = onChange },         [onChange])

  const [micStatus, setMicStatus]       = useState('loading') // loading|ready|listening|processing|muted|error
  const [muted, setMuted]               = useState(false)

  const streamRef        = useRef(null)
  const audioCtxRef      = useRef(null)
  const analyserRef      = useRef(null)
  const recorderRef      = useRef(null)
  const chunksRef        = useRef([])
  const rafRef           = useRef(null)
  const silenceTimerRef  = useRef(null)
  const speechStartRef   = useRef(null)
  const isTranscribingRef = useRef(false)
  const mutedRef         = useRef(false)

  // ── Transcribe a finished recording ──────────────────────────────────────────
  const handleRecordingReady = useCallback(async (blob) => {
    if (!blob.size || isTranscribingRef.current) return
    isTranscribingRef.current = true
    setMicStatus('processing')
    try {
      const float32 = await blobToFloat32(blob)
      // Discard near-silent recordings (e.g. background noise blips)
      const rms = Math.sqrt(float32.reduce((s, v) => s + v * v, 0) / float32.length)
      if (rms < 0.01) {
        logger.debug('WHISPER', 'Skipping silent segment')
        return
      }
      const text = await transcribeFloat32(float32)
      if (text && !isProcessingRef.current) {
        onChangeRef.current(text)
        onSendRef.current(text)
      } else if (text && isProcessingRef.current) {
        logger.warn('WHISPER', 'Command dropped — still processing previous')
      }
    } catch (err) {
      logger.error('WHISPER', `Transcription failed: ${err.message}`)
    } finally {
      isTranscribingRef.current = false
      if (!mutedRef.current) setMicStatus('listening')
    }
  }, [])

  // ── Start a fresh MediaRecorder segment ──────────────────────────────────────
  const startSegment = useCallback(() => {
    if (!streamRef.current) return
    chunksRef.current = []
    const rec = new MediaRecorder(streamRef.current)
    rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
    rec.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: rec.mimeType })
      handleRecordingReady(blob)
    }
    rec.start()
    recorderRef.current = rec
  }, [handleRecordingReady])

  const stopSegment = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop()
      recorderRef.current = null
    }
  }, [])

  // ── Silence detection loop (requestAnimationFrame) ────────────────────────────
  const startSilenceLoop = useCallback(() => {
    const analyser = analyserRef.current
    if (!analyser) return
    const data = new Uint8Array(analyser.frequencyBinCount)
    let speaking = false

    const tick = () => {
      if (mutedRef.current) { rafRef.current = null; return }
      analyser.getByteFrequencyData(data)
      const vol = data.reduce((a, b) => a + b, 0) / data.length

      if (vol > VOLUME_THRESHOLD) {
        // ── speech detected ──────────────────────────────────────────────────
        clearTimeout(silenceTimerRef.current)
        silenceTimerRef.current = null
        if (!speaking) {
          speaking = true
          speechStartRef.current = Date.now()
          logger.debug('VOICE', `Speech started (vol=${vol.toFixed(1)})`)
          startSegment()
        }
      } else if (speaking) {
        // ── silence after speech ─────────────────────────────────────────────
        if (!silenceTimerRef.current) {
          silenceTimerRef.current = setTimeout(() => {
            const duration = Date.now() - (speechStartRef.current || 0)
            speaking = false
            silenceTimerRef.current = null
            if (duration >= SPEECH_MIN_MS) {
              logger.debug('VOICE', `Silence detected after ${duration}ms — sending for transcription`)
              stopSegment()
            } else {
              logger.debug('VOICE', `Ignoring short blip (${duration}ms)`)
              stopSegment()
            }
            startSegment() // ready for next phrase
          }, SILENCE_AFTER_MS)
        }
      }

      rafRef.current = requestAnimationFrame(tick)
    }

    rafRef.current = requestAnimationFrame(tick)
  }, [startSegment, stopSegment])

  // ── Boot: load Whisper, open mic ──────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false

    const init = async () => {
      try {
        await loadWhisper((status) => {
          if (!cancelled) setMicStatus(status === 'ready' ? 'ready' : 'loading')
        })
        if (cancelled) return

        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        streamRef.current = stream

        const ctx = new AudioContext()
        audioCtxRef.current = ctx
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        analyserRef.current = analyser
        ctx.createMediaStreamSource(stream).connect(analyser)

        logger.info('VOICE', 'Mic open — starting silence detection')
        setMicStatus('listening')
        startSegment()
        startSilenceLoop()
      } catch (err) {
        if (!cancelled) {
          logger.error('VOICE', `Init failed: ${err.message}`)
          setMicStatus('error')
        }
      }
    }

    init()

    return () => {
      cancelled = true
      cancelAnimationFrame(rafRef.current)
      clearTimeout(silenceTimerRef.current)
      stopSegment()
      streamRef.current?.getTracks().forEach(t => t.stop())
      audioCtxRef.current?.close()
      streamRef.current = null
      audioCtxRef.current = null
      analyserRef.current = null
    }
  }, [startSilenceLoop, startSegment, stopSegment])

  // ── Mute toggle ───────────────────────────────────────────────────────────────
  const toggleMic = useCallback(() => {
    const nowMuted = !mutedRef.current
    mutedRef.current = nowMuted
    setMuted(nowMuted)
    if (nowMuted) {
      cancelAnimationFrame(rafRef.current)
      clearTimeout(silenceTimerRef.current)
      stopSegment()
      setMicStatus('muted')
      logger.info('VOICE', 'Mic muted')
    } else {
      setMicStatus('listening')
      startSegment()
      startSilenceLoop()
      logger.info('VOICE', 'Mic unmuted')
    }
  }, [startSegment, startSilenceLoop, stopSegment])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isProcessing && value.trim()) onSend(value)
    }
  }

  // ── Status-dependent UI ───────────────────────────────────────────────────────
  const STATUS = {
    loading:    { icon: '⏳', label: 'Loading Whisper model...', color: 'rgba(0,212,255,0.45)' },
    ready:      { icon: '🎤', label: 'Initializing mic...',       color: 'rgba(0,212,255,0.45)' },
    listening:  { icon: '🎙', label: 'Listening... speak anytime', color: '#00d4ff',              pulse: true },
    processing: { icon: '⟳', label: 'Transcribing...',            color: '#ffaa00',              spin: true  },
    muted:      { icon: '🔇', label: 'Mic muted — click to unmute', color: 'rgba(0,212,255,0.3)' },
    error:      { icon: '⚠️', label: 'Mic error — check permissions', color: '#ff4444'            },
  }

  const s = STATUS[micStatus] || STATUS.ready

  return (
    <div style={{ width: '100%', maxWidth: '680px' }}>
      <div className={`input-bar${micStatus === 'listening' ? ' input-bar--listening' : ''}`}>
        <button
          className={`mic-btn${micStatus === 'listening' ? ' active' : ''}`}
          onClick={toggleMic}
          disabled={micStatus === 'loading' || micStatus === 'error'}
          title={s.label}
          style={{ color: s.color }}
        >
          {s.pulse ? (
            <motion.span
              animate={{ scale: [1, 1.25, 1] }}
              transition={{ duration: 0.7, repeat: Infinity }}
              style={{ display: 'inline-block' }}
            >
              {s.icon}
            </motion.span>
          ) : s.spin ? (
            <motion.span
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              style={{ display: 'inline-block' }}
            >
              ⟳
            </motion.span>
          ) : s.icon}
        </button>

        <input
          className="input-field"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={s.label}
          disabled={isProcessing}
        />

        <button
          className="send-btn"
          onClick={() => onSend(value)}
          disabled={isProcessing || !value.trim()}
          title="Send"
        >
          {isProcessing ? (
            <motion.span
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              style={{ display: 'inline-block' }}
            >
              ⟳
            </motion.span>
          ) : '➤'}
        </button>
      </div>

      {micStatus === 'loading' && (
        <div style={{ textAlign: 'center', marginTop: 6, fontSize: 10, color: 'rgba(0,212,255,0.4)', fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>
          LOADING LOCAL WHISPER MODEL — ONE-TIME STARTUP DELAY
        </div>
      )}
    </div>
  )
}
