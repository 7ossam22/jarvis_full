import React, { useRef, useState, useCallback } from 'react'
import { motion } from 'framer-motion'

const hasSpeechAPI = !!(window.SpeechRecognition || window.webkitSpeechRecognition)

export default function InputBar({ value, onChange, onSend, isProcessing }) {
  const inputRef = useRef(null)
  const recognitionRef = useRef(null)
  const [isListening, setIsListening] = useState(false)

  const startListening = useCallback(() => {
    if (!hasSpeechAPI || isProcessing) return

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    const rec = new SpeechRecognition()
    rec.continuous = false
    rec.interimResults = false
    rec.lang = 'en-US'

    rec.onstart = () => setIsListening(true)
    rec.onend = () => { setIsListening(false); recognitionRef.current = null }
    rec.onerror = () => { setIsListening(false); recognitionRef.current = null }
    rec.onresult = (e) => {
      const transcript = e.results[0][0].transcript.trim()
      if (transcript) {
        onChange(transcript)
        onSend(transcript)
      }
    }

    recognitionRef.current = rec
    rec.start()
  }, [isProcessing, onChange, onSend])

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
  }, [])

  const handleMicClick = useCallback(() => {
    if (isListening) stopListening()
    else startListening()
  }, [isListening, startListening, stopListening])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isProcessing && value.trim()) onSend(value)
    }
  }

  return (
    <div style={{ width: '100%', maxWidth: '680px' }}>
      <div className={`input-bar${isListening ? ' input-bar--listening' : ''}`}>
        {hasSpeechAPI && (
          <button
            className={`mic-btn${isListening ? ' active' : ''}`}
            onClick={handleMicClick}
            disabled={isProcessing}
            title={isListening ? 'Stop listening' : 'Speak to JARVIS (click to activate)'}
          >
            {isListening ? (
              <motion.span
                animate={{ scale: [1, 1.25, 1] }}
                transition={{ duration: 0.7, repeat: Infinity }}
                style={{ display: 'inline-block' }}
              >
                🎙
              </motion.span>
            ) : '🎤'}
          </button>
        )}

        <input
          ref={inputRef}
          className="input-field"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isListening ? 'Listening...' : 'Ask JARVIS anything...'}
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
    </div>
  )
}
