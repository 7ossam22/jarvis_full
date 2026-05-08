import React, { useState } from 'react'

const CONTROLS = [
  { action: 'previous',    label: '⏮', title: 'Previous' },
  { action: 'play-pause',  label: '⏯', title: 'Play / Pause' },
  { action: 'next',        label: '⏭', title: 'Next' },
  { action: 'volume-down', label: '🔉', title: 'Volume Down' },
  { action: 'volume-up',   label: '🔊', title: 'Volume Up' },
  { action: 'mute',        label: '🔇', title: 'Mute' },
]

export default function SpotifyControls() {
  const [active, setActive] = useState(null)

  const send = async (action) => {
    setActive(action)
    if (window.electronAPI?.spotifyControl) {
      await window.electronAPI.spotifyControl({ action })
    }
    setTimeout(() => setActive(null), 300)
  }

  return (
    <div className="widget">
      <div className="widget-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ color: '#1db954', fontSize: 10 }}>●</span>
        MEDIA CONTROLS
      </div>

      <div style={{ display: 'flex', gap: 5, justifyContent: 'center', flexWrap: 'wrap' }}>
        {CONTROLS.map(({ action, label, title }) => (
          <button
            key={action}
            onClick={() => send(action)}
            title={title}
            style={{
              background: active === action
                ? 'rgba(0,212,255,0.25)'
                : 'rgba(0,212,255,0.07)',
              border: `1px solid ${active === action ? 'rgba(0,212,255,0.7)' : 'rgba(0,212,255,0.25)'}`,
              borderRadius: 6,
              color: '#00d4ff',
              fontSize: 16,
              width: 38,
              height: 34,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all 0.15s',
              flexShrink: 0,
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(0,212,255,0.18)' }}
            onMouseLeave={e => { e.currentTarget.style.background = active === action ? 'rgba(0,212,255,0.25)' : 'rgba(0,212,255,0.07)' }}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={{
        textAlign: 'center',
        fontSize: 8,
        color: 'rgba(0,212,255,0.35)',
        fontFamily: "'Share Tech Mono', monospace",
        marginTop: 6,
        letterSpacing: '0.8px',
        lineHeight: 1.5,
      }}>
        SAY: "next song" · "pause" · "louder" · "skip"
      </div>
    </div>
  )
}
