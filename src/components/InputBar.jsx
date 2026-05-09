import React, { useRef } from 'react'
import { motion } from 'framer-motion'

export default function InputBar({ value, onChange, onSend, isProcessing }) {
  const inputRef = useRef(null)

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isProcessing && value.trim()) onSend()
    }
  }

  return (
    <div style={{ width: '100%', maxWidth: '680px' }}>
      <div className="input-bar">
        <input
          ref={inputRef}
          className="input-field"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask JARVIS anything..."
          disabled={isProcessing}
        />

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
