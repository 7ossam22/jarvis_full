import React, { useState, useEffect, useRef } from 'react'
import { logger } from '../services/logger'

const LEVEL_COLORS = {
  DEBUG: 'rgba(0, 212, 255, 0.5)',
  INFO:  '#00d4ff',
  WARN:  '#ffaa00',
  ERROR: '#ff4444',
}

export default function LogViewer({ visible, onClose }) {
  const [entries, setEntries] = useState(() => logger.getEntries())
  const [filter, setFilter] = useState('ALL')
  const bottomRef = useRef(null)

  useEffect(() => {
    const unsub = logger.subscribe(setEntries)
    return unsub
  }, [])

  useEffect(() => {
    if (visible && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [visible, entries])

  if (!visible) return null

  const filtered = filter === 'ALL' ? entries : entries.filter(e => e.level === filter)

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      zIndex: 10000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'rgba(0, 0, 0, 0.75)',
      backdropFilter: 'blur(4px)',
    }}>
      <div style={{
        width: '80vw',
        maxWidth: 900,
        height: '70vh',
        background: 'rgba(5, 15, 25, 0.97)',
        border: '1px solid rgba(0, 212, 255, 0.35)',
        borderRadius: 12,
        boxShadow: '0 0 40px rgba(0, 212, 255, 0.15)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          borderBottom: '1px solid rgba(0, 212, 255, 0.2)',
          flexShrink: 0,
        }}>
          <span style={{ color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: 13, letterSpacing: 2 }}>
            ◈ SYSTEM DEBUG LOG
          </span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {['ALL', 'INFO', 'WARN', 'ERROR', 'DEBUG'].map(lvl => (
              <button
                key={lvl}
                onClick={() => setFilter(lvl)}
                style={{
                  background: filter === lvl ? 'rgba(0,212,255,0.2)' : 'transparent',
                  border: `1px solid ${filter === lvl ? 'rgba(0,212,255,0.6)' : 'rgba(0,212,255,0.2)'}`,
                  borderRadius: 4,
                  color: lvl === 'ALL' ? '#00d4ff' : (LEVEL_COLORS[lvl] || '#00d4ff'),
                  fontSize: 10,
                  padding: '3px 8px',
                  cursor: 'pointer',
                  fontFamily: "'Share Tech Mono', monospace",
                  letterSpacing: 1,
                }}
              >
                {lvl}
              </button>
            ))}
            <button
              onClick={() => logger.clear()}
              style={{ background: 'transparent', border: '1px solid rgba(255,68,68,0.4)', borderRadius: 4, color: '#ff4444', fontSize: 10, padding: '3px 8px', cursor: 'pointer', fontFamily: "'Share Tech Mono', monospace" }}
            >
              CLEAR
            </button>
            <button
              onClick={onClose}
              style={{ background: 'transparent', border: '1px solid rgba(0,212,255,0.3)', borderRadius: 4, color: '#00d4ff', fontSize: 12, padding: '3px 8px', cursor: 'pointer' }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Log entries */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0', fontFamily: "'Share Tech Mono', monospace", fontSize: 11 }}>
          {filtered.length === 0 ? (
            <div style={{ color: 'rgba(0,212,255,0.3)', textAlign: 'center', padding: 32 }}>No log entries</div>
          ) : (
            filtered.map(entry => (
              <div key={entry.id} style={{
                display: 'flex',
                gap: 12,
                padding: '4px 16px',
                borderBottom: '1px solid rgba(0,212,255,0.05)',
                alignItems: 'flex-start',
              }}>
                <span style={{ color: 'rgba(0,212,255,0.35)', flexShrink: 0, fontSize: 10 }}>
                  {entry.timestamp.slice(11, 23)}
                </span>
                <span style={{ color: LEVEL_COLORS[entry.level] || '#00d4ff', flexShrink: 0, width: 42, fontSize: 10 }}>
                  {entry.level}
                </span>
                <span style={{ color: 'rgba(0,212,255,0.55)', flexShrink: 0, width: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 10 }}>
                  {entry.category}
                </span>
                <div style={{ flex: 1, color: 'rgba(220, 240, 255, 0.85)' }}>
                  <div>{entry.message}</div>
                  {entry.data && (
                    <pre style={{ margin: '4px 0 0', color: 'rgba(0,212,255,0.4)', fontSize: 10, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                      {entry.data}
                    </pre>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ padding: '8px 16px', borderTop: '1px solid rgba(0,212,255,0.1)', color: 'rgba(0,212,255,0.3)', fontSize: 10, fontFamily: "'Share Tech Mono', monospace", flexShrink: 0 }}>
          SAY "HIDE LOGS" OR "CLOSE LOGS" TO DISMISS · {entries.length} ENTRIES
        </div>
      </div>
    </div>
  )
}
