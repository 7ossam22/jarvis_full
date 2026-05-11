import React, { useRef, useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { logger } from '../services/logger'

const hasSpeechAPI = !!(window.SpeechRecognition || window.webkitSpeechRecognition)

export default function InputBar({ value, onChange, onSend, isProcessing }) {
  const inputRef = useRef(null)
  const recognitionRef = useRef(null)
  const shouldListenRef = useRef(true)
  const restartTimerRef = useRef(null)
  const isProcessingRef = useRef(isProcessing)
  const onSendRef = useRef(onSend)
  const onChangeRef = useRef(onChange)
  const [isListening, setIsListening] = useState(false)
  const [muted, setMuted] = useState(false)

  useEffect(() => { isProcessingRef.current = isProcessing }, [isProcessing])
  useEffect(() => { onSendRef.current = onSend }, [onSend])
  useEffect(() => { onChangeRef.current = onChange }, [onChange])

  const startRecognition = useCallback(() => {
    if (!hasSpeechAPI || recognitionRef.current) return

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    const rec = new SpeechRecognition()
    rec.continuous = true
    rec.interimResults = false
    rec.lang = 'en-US'
    rec.maxAlternatives = 1

    rec.onstart = () => {
      setIsListening(true)
      logger.info('VOICE', 'Recognition started — listening continuously')
    }

    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) {
          const transcript = e.results[i][0].transcript.trim()
          if (!transcript) continue
          logger.info('VOICE', `Heard: "${transcript}"`, { processing: isProcessingRef.current })
          if (isProcessingRef.current) {
            logger.warn('VOICE', 'Command dropped — still processing previous command')
          } else {
            onChangeRef.current(transcript)
            onSendRef.current(transcript)
          }
        }
      }
    }

    rec.onerror = (e) => {
      logger.warn('VOICE', `Recognition error: ${e.error}`)
      // no-speech and audio-capture are non-fatal — recognition continues
      if (e.error === 'no-speech' || e.error === 'audio-capture') return
      recognitionRef.current = null
      setIsListening(false)
    }

    rec.onend = () => {
      recognitionRef.current = null
      setIsListening(false)
      if (shouldListenRef.current) {
        logger.debug('VOICE', 'Recognition ended unexpectedly — restarting in 500ms')
        restartTimerRef.current = setTimeout(startRecognition, 500)
      }
    }

    try {
      rec.start()
      recognitionRef.current = rec
      logger.debug('VOICE', 'Recognition instance started')
    } catch (err) {
      logger.error('VOICE', `Failed to start recognition: ${err.message}`)
      recognitionRef.current = null
    }
  }, [])

  // Auto-start on mount, auto-cleanup on unmount
  useEffect(() => {
    if (hasSpeechAPI) {
      logger.info('VOICE', 'InputBar mounted — starting continuous recognition')
      shouldListenRef.current = true
      startRecognition()
    } else {
      logger.warn('VOICE', 'Web Speech API not available in this environment')
    }
    return () => {
      shouldListenRef.current = false
      clearTimeout(restartTimerRef.current)
      if (recognitionRef.current) {
        try { recognitionRef.current.stop() } catch {}
        recognitionRef.current = null
      }
    }
  }, [startRecognition])

  const toggleMic = useCallback(() => {
    if (muted) {
      setMuted(false)
      shouldListenRef.current = true
      startRecognition()
      logger.info('VOICE', 'Mic unmuted — resuming recognition')
    } else {
      setMuted(true)
      shouldListenRef.current = false
      clearTimeout(restartTimerRef.current)
      if (recognitionRef.current) {
        try { recognitionRef.current.stop() } catch {}
        recognitionRef.current = null
      }
      setIsListening(false)
      logger.info('VOICE', 'Mic muted — recognition paused')
    }
  }, [muted, startRecognition])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isProcessing && value.trim()) onSend(value)
    }
  }

  return (
    <div style={{ width: '100%', maxWidth: '680px' }}>
      <div className={`input-bar${isListening && !muted ? ' input-bar--listening' : ''}`}>
        {hasSpeechAPI && (
          <button
            className={`mic-btn${isListening && !muted ? ' active' : ''}`}
            onClick={toggleMic}
            title={muted ? 'Mic muted — click to unmute' : isListening ? 'Always listening — click to mute' : 'Starting mic...'}
          >
            {muted
              ? '🔇'
              : isListening
                ? (
                  <motion.span
                    animate={{ scale: [1, 1.25, 1] }}
                    transition={{ duration: 0.7, repeat: Infinity }}
                    style={{ display: 'inline-block' }}
                  >
                    🎙
                  </motion.span>
                )
                : '🎤'}
          </button>
        )}

        <input
          ref={inputRef}
          className="input-field"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={muted ? 'Mic muted — click 🔇 to unmute' : isListening ? 'Listening... speak anytime' : 'Ask JARVIS anything...'}
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

      {!hasSpeechAPI && (
        <div style={{ textAlign: 'center', marginTop: 6, fontSize: 10, color: 'rgba(255,100,100,0.6)', fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>
          SPEECH API UNAVAILABLE — TYPE COMMANDS MANUALLY
        </div>
      )}
    </div>
  )
}
