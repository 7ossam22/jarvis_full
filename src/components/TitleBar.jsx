import React from 'react'
import ProviderSwitcher from './ProviderSwitcher'
import { useSettings } from '../context/SettingsContext'

export default function TitleBar({ provider, onProviderChange }) {
  const { setOpen } = useSettings()

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
          <span className="status-dot" style={{ background: '#00ff88', boxShadow: '0 0 6px #00ff88' }} />
          <span style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: 9, color: 'var(--text-muted)', letterSpacing: 1 }}>
            ONLINE
          </span>
        </div>
        <button className="window-btn" onClick={() => setOpen(true)} title="Settings" style={{ marginRight: 4 }}>
          ⚙
        </button>
        <button className="window-btn minimize" onClick={() => window.electronAPI?.minimizeWindow()} title="Minimize">&#8211;</button>
        <button className="window-btn maximize" onClick={() => window.electronAPI?.maximizeWindow()} title="Maximize">&#9633;</button>
        <button className="window-btn close"    onClick={() => window.electronAPI?.closeWindow()}    title="Close">&#10005;</button>
      </div>
    </div>
  )
}
