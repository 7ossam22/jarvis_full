import React, { useRef, useEffect } from 'react'

export default function Camera({ active, onToggle }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)

  useEffect(() => {
    if (active) {
      startCamera()
    } else {
      stopCamera()
    }
    return () => stopCamera()
  }, [active])

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 360 }, facingMode: 'user' },
        audio: false,
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }
    } catch (err) {
      console.warn('Camera access denied:', err.message)
    }
  }

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null
    }
  }

  return (
    <div className="camera-panel panel">
      <div className="panel-header">
        <span className="panel-title">Camera Feed</span>
        <span className="panel-indicator" style={{ background: active ? '#ff4444' : 'var(--cyan-primary)', boxShadow: `0 0 8px ${active ? '#ff4444' : 'var(--cyan-primary)'}` }} />
      </div>

      <div className="camera-content">
        <div className="camera-feed">
          {active ? (
            <>
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '7px' }}
              />
              <div className="camera-active-badge">
                <div className="camera-live-dot" />
                <span className="camera-live-text">LIVE</span>
              </div>
            </>
          ) : (
            <div className="camera-offline">
              <div className="camera-offline-icon">📷</div>
              <div className="camera-offline-text">CAMERA OFFLINE</div>
            </div>
          )}
        </div>

        <button
          className={`camera-toggle-btn ${active ? 'active' : ''}`}
          onClick={onToggle}
        >
          {active ? '⬛ DISABLE CAMERA' : '▶ ENABLE CAMERA'}
        </button>
      </div>
    </div>
  )
}
