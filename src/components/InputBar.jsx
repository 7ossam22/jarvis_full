import React, { useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

export default function InputBar({ value, onChange, onSend, onMicToggle, isListening, isProcessing, directMode, transcript, voiceError }) {
  const inputRef = useRef(null)

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isProcessing && value.trim()) {
        onSend()
      }
    }
  }

  return (
    <div style={{ width: '100%', maxWidth: '680px', position: 'relative' }}>
      <AnimatePresence>
        {voiceError && (
          <motion.div
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            style={{
              position: 'absolute',
              bottom: '100%',
              left: 0,
              right: 0,
              textAlign: 'center',
              paddingBottom: '8px',
              fontFamily: "'Share Tech Mono', monospace",
              fontSize: '10px',
              color: '#ff4444',
              letterSpacing: '0.5px',
              pointerEvents: 'none',
            }}
          >
            ⚠ {voiceError}
          </motion.div>
        )}
        {!voiceError && transcript && (
          <motion.div
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 5 }}
            style={{
              position: 'absolute',
              bottom: '100%',
              left: 0,
              right: 0,
              textAlign: 'center',
              paddingBottom: '8px',
              fontFamily: "'Share Tech Mono', monospace",
              fontSize: '11px',
              color: 'rgba(0, 212, 255, 0.6)',
              letterSpacing: '1px',
              pointerEvents: 'none',
            }}
          >
            "{transcript}"
          </motion.div>
        )}
      </AnimatePresence>

      <div className="input-bar">
        {isListening && (
          <div className="listening-indicator">
            <div className="voice-wave">
              <span /><span /><span /><span /><span />
            </div>
          </div>
        )}

        <input
          ref={inputRef}
          className="input-field"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            directMode
              ? 'Speak your command now...'
              : isListening
                ? 'Listening... say "Jarvis" + command, or type'
                : 'Ask JARVIS anything... or say "Jarvis"'
          }
          disabled={isProcessing}
        />

        <button
          className={`mic-btn ${isListening ? 'active' : ''}`}
          onClick={onMicToggle}
          title={isListening ? 'Stop listening' : 'Start listening'}
        >
          {isListening ? '🔴' : '🎤'}
        </button>

        <button
          className="send-btn"
          onClick={onSend}
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
