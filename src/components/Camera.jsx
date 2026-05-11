import React, { useRef, useEffect } from 'react'

export default function CameraOverlay({ active, onClose }) {
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
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
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

  if (!active) return null

  return (
    <div className="camera-fullscreen-overlay">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="camera-fullscreen-video"
      />
      <div className="camera-vignette" />
      <div className="camera-overlay-ui">
        <div className="camera-live-indicator">
          <div className="camera-live-dot" />
          <span className="camera-live-text">LIVE FEED</span>
        </div>
        <div className="camera-close-hint">SAY "CLOSE CAMERA" OR "I'M DONE" TO DISMISS</div>
      </div>
    </div>
  )
}
