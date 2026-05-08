import React from 'react'
import ProviderSwitcher from './ProviderSwitcher'

export default function TitleBar({ isListening, provider, onProviderChange }) {
  const handleMinimize = () => {
    if (window.electronAPI) window.electronAPI.minimizeWindow()
  }

  const handleMaximize = () => {
    if (window.electronAPI) window.electronAPI.maximizeWindow()
  }

  const handleClose = () => {
    if (window.electronAPI) window.electronAPI.closeWindow()
  }

  return (
    <div className="title-bar">
      <div className="title-bar-left">
        <div className="title-bar-logo">J</div>
        <div>
          <div className="title-bar-title">J.A.R.V.I.S</div>
          <div className="title-bar-subtitle">JUST A RATHER VERY INTELLIGENT SYSTEM</div>
        </div>
      </div>

      <div className="title-bar-center">
        <ProviderSwitcher provider={provider} onChange={onProviderChange} />
      </div>

      <div className="title-bar-controls">
        <div className="title-bar-status" style={{ marginRight: 10 }}>
          <span
            className="status-dot"
            style={{
              background: isListening ? '#00d4ff' : '#00ff88',
              boxShadow: `0 0 6px ${isListening ? '#00d4ff' : '#00ff88'}`,
            }}
          />
          <span style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>
            {isListening ? 'LISTENING' : 'ONLINE'}
          </span>
        </div>
        <button className="window-btn minimize" onClick={handleMinimize} title="Minimize">
          &#8211;
        </button>
        <button className="window-btn maximize" onClick={handleMaximize} title="Maximize">
          &#9633;
        </button>
        <button className="window-btn close" onClick={handleClose} title="Close">
          &#10005;
        </button>
      </div>
    </div>
  )
}
