import React, { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

function formatTime(date) {
  return new Date(date).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function MessageItem({ message }) {
  const isUser = message.role === 'user'

  return (
    <motion.div
      className={`message-item ${message.role}`}
      initial={{ opacity: 0, x: isUser ? 20 : -20, y: 10 }}
      animate={{ opacity: 1, x: 0, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      layout
    >
      <span className="message-role">{isUser ? 'YOU' : 'JARVIS'}</span>

      {message.loading ? (
        <div className="message-bubble" style={{ background: 'rgba(15,25,40,0.8)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: '14px 14px 14px 4px' }}>
          <div className="message-loading">
            <div className="loading-dot" />
            <div className="loading-dot" />
            <div className="loading-dot" />
          </div>
        </div>
      ) : (
        <div className="message-bubble">
          {message.content}
        </div>
      )}

      {!message.loading && (
        <span className="message-time">{formatTime(message.timestamp)}</span>
      )}
    </motion.div>
  )
}

export default function Conversation({ messages, isProcessing }) {
  const bottomRef = useRef(null)
  const containerRef = useRef(null)

  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isProcessing])

  const displayMessages = [...messages]
  if (isProcessing) {
    displayMessages.push({
      id: 'loading',
      role: 'assistant',
      loading: true,
      timestamp: Date.now(),
    })
  }

  return (
    <div className="conversation-panel panel">
      <div className="panel-header">
        <span className="panel-title">Conversation Log</span>
        <span className="panel-indicator" />
      </div>

      <div className="messages-list" ref={containerRef}>
        {displayMessages.length === 0 ? (
          <div className="conversation-empty">
            <div className="conversation-empty-icon">◎</div>
            <div className="conversation-empty-text">
              Say "Jarvis" to wake me up,<br />
              or type your command below.
            </div>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {displayMessages.map((msg, i) => (
              <MessageItem key={msg.id || i} message={msg} />
            ))}
          </AnimatePresence>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
