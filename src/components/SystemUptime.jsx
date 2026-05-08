import React, { useState, useEffect } from 'react'

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function SystemUptime() {
  const [uptime, setUptime] = useState(0)
  const [sessionTime, setSessionTime] = useState(0)
  const [platform, setPlatform] = useState('LINUX')

  useEffect(() => {
    const sessionStart = Date.now()

    const fetchUptime = async () => {
      try {
        if (window.electronAPI) {
          const seconds = await window.electronAPI.getUptime()
          setUptime(seconds)
          const info = await window.electronAPI.getSystemInfo()
          setPlatform(info.platform?.toUpperCase() ?? 'LINUX')
        } else {
          setUptime(prev => prev + 3)
        }
      } catch {
        setUptime(prev => prev + 3)
      }
      setSessionTime(Math.floor((Date.now() - sessionStart) / 1000))
    }

    fetchUptime()
    const interval = setInterval(fetchUptime, 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="uptime-panel panel" style={{ flexShrink: 0 }}>
      <div className="panel-header">
        <span className="panel-title">System Uptime</span>
        <span className="panel-indicator" />
      </div>
      <div className="uptime-content">
        <div className="uptime-time">{formatUptime(uptime)}</div>
        <div className="uptime-bar">
          <div className="uptime-bar-fill" />
        </div>
        <div className="uptime-meta">
          <div className="uptime-meta-item">
            <span className="uptime-meta-label">PLATFORM</span>
            <span className="uptime-meta-value">{platform}</span>
          </div>
          <div className="uptime-meta-item" style={{ alignItems: 'flex-end' }}>
            <span className="uptime-meta-label">SESSION</span>
            <span className="uptime-meta-value">{formatUptime(sessionTime)}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
