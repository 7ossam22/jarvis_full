import React, { useRef, useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import { logger } from '../services/logger'

const hasSpeechAPI = !!(window.SpeechRecognition || window.webkitSpeechRecognition)
const MAX_NETWORK_RETRIES = 5

export default function InputBar({ value, onChange, onSend, isProcessing }) {
  const inputRef = useRef(null)
  const recognitionRef = useRef(null)
  const shouldListenRef = useRef(true)
  const restartTimerRef = useRef(null)
  const isProcessingRef = useRef(isProcessing)
  const onSendRef = useRef(onSend)
  const onChangeRef = useRef(onChange)
  const networkErrorCountRef = useRef(0)

  const [isListening, setIsListening] = useState(false)
  const [muted, setMuted] = useState(false)
  const [networkFailed, setNetworkFailed] = useState(false)

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
      networkErrorCountRef.current = 0
      setIsListening(true)
      setNetworkFailed(false)
      logger.info('VOICE', 'Recognition started')
    }

    rec.onresult = (e) => {
      networkErrorCountRef.current = 0
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) {
          const transcript = e.results[i][0].transcript.trim()
          if (!transcript) continue
          logger.info('VOICE', `Heard: "${transcript}"`, { processing: isProcessingRef.current })
          if (isProcessingRef.current) {
            logger.warn('VOICE', 'Command dropped — still processing previous')
          } else {
            onChangeRef.current(transcript)
            onSendRef.current(transcript)
          }
        }
      }
    }

    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'audio-capture') return

      if (e.error === 'network') {
        networkErrorCountRef.current++
        logger.warn('VOICE', `Network error #${networkErrorCountRef.current} — speech API cannot reach Google servers`)
        if (networkErrorCountRef.current >= MAX_NETWORK_RETRIES) {
          shouldListenRef.current = false
          setNetworkFailed(true)
          setIsListening(false)
          logger.error('VOICE', 'Too many network errors — stopping recognition. Check internet connection or use text input.')
        }
        return
      }

      logger.warn('VOICE', `Recognition error: ${e.error}`)
      recognitionRef.current = null
      setIsListening(false)
    }

    rec.onend = () => {
      recognitionRef.current = null
      setIsListening(false)

      if (!shouldListenRef.current) return

      // Exponential backoff for network errors: 1s, 2s, 4s, 8s, 16s
      const delay = networkErrorCountRef.current > 0
        ? Math.min(1000 * Math.pow(2, networkErrorCountRef.current - 1), 16000)
        : 500

      if (networkErrorCountRef.current > 0) {
        logger.debug('VOICE', `Backoff restart in ${delay}ms (network errors: ${networkErrorCountRef.current})`)
      }

      restartTimerRef.current = setTimeout(startRecognition, delay)
    }

    try {
      rec.start()
      recognitionRef.current = rec
    } catch (err) {
      logger.error('VOICE', `Failed to start: ${err.message}`)
      recognitionRef.current = null
    }
  }, [])

  useEffect(() => {
    if (hasSpeechAPI) {
      shouldListenRef.current = true
      startRecognition()
    } else {
      logger.warn('VOICE', 'Web Speech API not available')
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
    if (muted || networkFailed) {
      setMuted(false)
      setNetworkFailed(false)
      networkErrorCountRef.current = 0
      shouldListenRef.current = true
      startRecognition()
      logger.info('VOICE', 'Mic re-enabled manually')
    } else {
      setMuted(true)
      shouldListenRef.current = false
      clearTimeout(restartTimerRef.current)
      if (recognitionRef.current) {
        try { recognitionRef.current.stop() } catch {}
        recognitionRef.current = null
      }
      setIsListening(false)
      logger.info('VOICE', 'Mic muted')
    }
  }, [muted, networkFailed, startRecognition])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isProcessing && value.trim()) onSend(value)
    }
  }

  const micIcon = networkFailed
    ? '⚠️'
    : muted
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
        : '🎤'

  const micTitle = networkFailed
    ? 'Speech API network error — click to retry'
    : muted
      ? 'Mic muted — click to unmute'
      : isListening
        ? 'Listening — click to mute'
        : 'Starting mic...'

  const placeholder = networkFailed
    ? 'Speech unavailable (network error) — type here or click ⚠️ to retry'
    : muted
      ? 'Mic muted — click 🔇 to unmute'
      : isListening
        ? 'Listening... speak anytime'
        : 'Ask JARVIS anything...'

  return (
    <div style={{ width: '100%', maxWidth: '680px' }}>
      <div className={`input-bar${isListening && !muted && !networkFailed ? ' input-bar--listening' : ''}`}>
        {hasSpeechAPI && (
          <button
            className={`mic-btn${isListening && !muted && !networkFailed ? ' active' : ''}`}
            onClick={toggleMic}
            title={micTitle}
            style={networkFailed ? { color: '#ffaa00' } : {}}
          >
            {micIcon}
          </button>
        )}

        <input
          ref={inputRef}
          className="input-field"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
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

      {networkFailed && (
        <div style={{ textAlign: 'center', marginTop: 6, fontSize: 10, color: '#ffaa00', fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>
          SPEECH API NETWORK ERROR — CHECK INTERNET · SAY "SHOW LOGS" OR CLICK ⚠️ TO RETRY
        </div>
      )}

      {!hasSpeechAPI && (
        <div style={{ textAlign: 'center', marginTop: 6, fontSize: 10, color: 'rgba(255,100,100,0.6)', fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>
          SPEECH API UNAVAILABLE — TYPE COMMANDS MANUALLY
        </div>
      )}
    </div>
  )
}
