import React, { useState, useEffect } from 'react'

const CIRCUMFERENCE = 2 * Math.PI * 28 // r=28 for 70px SVG

function RingGauge({ value, label, sublabel }) {
  const offset = CIRCUMFERENCE - (value / 100) * CIRCUMFERENCE
  const colorClass = value > 80 ? 'danger' : value > 60 ? 'warning' : ''

  return (
    <div className="stat-item">
      <div className="stat-ring-container">
        <svg className="stat-ring-svg" viewBox="0 0 70 70">
          <circle className="stat-ring-bg" cx="35" cy="35" r="28" />
          <circle
            className={`stat-ring-fill ${colorClass}`}
            cx="35"
            cy="35"
            r="28"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={offset}
          />
        </svg>
        <div className="stat-ring-value">
          <span className="stat-percent" style={{ color: value > 80 ? '#ff4444' : value > 60 ? '#ffaa00' : '#00d4ff', fontSize: '13px' }}>
            {value}%
          </span>
        </div>
      </div>
      <span className="stat-label">{label}</span>
      {sublabel && <span className="stat-sublabel">{sublabel}</span>}
    </div>
  )
}

export default function SystemStats() {
  const [stats, setStats] = useState({ cpu: 35, ram: 52, totalRam: 8, usedRam: 4 })

  useEffect(() => {
    const fetchStats = async () => {
      try {
        if (window.electronAPI) {
          const data = await window.electronAPI.getSystemInfo()
          setStats({
            cpu: data.cpu,
            ram: data.ram,
            totalRam: data.totalRam,
            usedRam: data.usedRam,
          })
        } else {
          // Browser fallback with simulated realistic values
          setStats(prev => ({
            cpu: Math.max(10, Math.min(85, prev.cpu + (Math.random() - 0.5) * 12)),
            ram: Math.max(20, Math.min(90, prev.ram + (Math.random() - 0.5) * 4)),
            totalRam: prev.totalRam,
            usedRam: Math.round((prev.ram / 100) * prev.totalRam),
          }))
        }
      } catch (err) {
        // keep previous values
      }
    }

    fetchStats()
    const interval = setInterval(fetchStats, 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="stats-panel panel" style={{ flexShrink: 0 }}>
      <div className="panel-header">
        <span className="panel-title">System Monitor</span>
        <span className="panel-indicator" />
      </div>
      <div className="stats-grid">
        <RingGauge
          value={Math.round(stats.cpu)}
          label="CPU"
          sublabel="CORE LOAD"
        />
        <RingGauge
          value={Math.round(stats.ram)}
          label="RAM"
          sublabel={`${stats.usedRam}/${stats.totalRam} GB`}
        />
        <div className="network-status">
          <div className="network-dot" />
          <span className="network-text">NETWORK ONLINE</span>
        </div>
      </div>
    </div>
  )
}
