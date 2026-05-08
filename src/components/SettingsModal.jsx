import React, { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useSettings } from '../context/SettingsContext'
import { pingLocalAI } from '../services/aiService'
import { ELEVENLABS_VOICES, ELEVENLABS_MODELS, speak } from '../services/ttsService'

export default function SettingsModal() {
  const { settings, save, open, setOpen } = useSettings()

  const [url, setUrl] = useState(settings.localAiUrl)
  const [model, setModel] = useState(settings.localAiModel)
  const [elKey, setElKey] = useState(settings.elevenLabsKey)
  const [voiceId, setVoiceId] = useState(settings.voiceId)
  const [ttsModel, setTtsModel] = useState(settings.ttsModel)
  const [micDeviceId, setMicDeviceId] = useState(settings.micDeviceId || '')
  const [pingStatus, setPingStatus] = useState(null)
  const [models, setModels] = useState([])
  const [saved, setSaved] = useState(false)
  const [testingVoice, setTestingVoice] = useState(false)

  // Microphone state
  const [micDevices, setMicDevices] = useState([])
  const [micPermission, setMicPermission] = useState('unknown') // 'unknown'|'granted'|'denied'
  const [micLevel, setMicLevel] = useState(0)
  const [testingMic, setTestingMic] = useState(false)
  const micTestStreamRef = useRef(null)
  const micAnimFrameRef = useRef(null)

  useEffect(() => {
    if (open) {
      setUrl(settings.localAiUrl)
      setModel(settings.localAiModel)
      setElKey(settings.elevenLabsKey)
      setVoiceId(settings.voiceId)
      setTtsModel(settings.ttsModel)
      setMicDeviceId(settings.micDeviceId || '')
      setPingStatus(null)
      setModels([])
      setSaved(false)
      loadMicDevices()
    } else {
      stopMicTest()
    }
  }, [open, settings])

  const loadMicDevices = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(t => t.stop())
      setMicPermission('granted')
      const devices = await navigator.mediaDevices.enumerateDevices()
      setMicDevices(devices.filter(d => d.kind === 'audioinput'))
    } catch {
      setMicPermission('denied')
    }
  }

  const startMicTest = async () => {
    if (testingMic) { stopMicTest(); return }
    try {
      const constraints = { audio: micDeviceId ? { deviceId: { exact: micDeviceId } } : true }
      const stream = await navigator.mediaDevices.getUserMedia(constraints)
      micTestStreamRef.current = stream
      setTestingMic(true)

      const ctx = new AudioContext()
      const src = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      src.connect(analyser)
      const buf = new Uint8Array(analyser.frequencyBinCount)

      const tick = () => {
        analyser.getByteFrequencyData(buf)
        const avg = buf.reduce((a, b) => a + b, 0) / buf.length
        setMicLevel(Math.min(100, Math.round(avg * 2.5)))
        micAnimFrameRef.current = requestAnimationFrame(tick)
      }
      tick()
    } catch (err) {
      setMicPermission('denied')
    }
  }

  const stopMicTest = () => {
    if (micAnimFrameRef.current) cancelAnimationFrame(micAnimFrameRef.current)
    if (micTestStreamRef.current) {
      micTestStreamRef.current.getTracks().forEach(t => t.stop())
      micTestStreamRef.current = null
    }
    setTestingMic(false)
    setMicLevel(0)
  }

  const testConnection = async () => {
    setPingStatus('testing')
    setModels([])
    const result = await pingLocalAI(url)
    setPingStatus(result.online ? 'online' : 'offline')
    if (result.models?.length > 0) {
      setModels(result.models)
      if (result.models.length === 1) setModel(result.models[0])
    }
  }

  const testVoice = async () => {
    setTestingVoice(true)
    try {
      await speak("Initializing J.A.R.V.I.S. voice systems. All systems nominal, Sir.", {
        elevenLabsKey: elKey, voiceId, ttsModel,
      })
    } finally {
      setTestingVoice(false)
    }
  }

  const handleSave = () => {
    save({ localAiUrl: url.trim(), localAiModel: model.trim(), elevenLabsKey: elKey.trim(), voiceId, ttsModel, micDeviceId })
    setSaved(true)
    stopMicTest()
    setTimeout(() => { setSaved(false); setOpen(false) }, 800)
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="settings-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setOpen(false)} />

          <motion.div className="settings-panel" initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ type: 'spring', stiffness: 300, damping: 30 }}>
            <div className="settings-header">
              <div>
                <div className="settings-title">⚙ SETTINGS</div>
                <div className="settings-subtitle">SYSTEM CONFIGURATION</div>
              </div>
              <button className="settings-close" onClick={() => setOpen(false)}>✕</button>
            </div>

            <div className="settings-body">

              {/* ── Microphone ── */}
              <div className="settings-section">
                <div className="settings-section-label">🎤 MICROPHONE INPUT</div>

                {micPermission === 'denied' && (
                  <div className="settings-hint" style={{ color: '#ff4444', marginBottom: 8 }}>
                    ⚠ Microphone access denied. Check browser/system permissions.
                  </div>
                )}

                <div className="settings-field">
                  <label className="settings-label">INPUT DEVICE</label>
                  <select
                    className="settings-input settings-select"
                    value={micDeviceId}
                    onChange={e => setMicDeviceId(e.target.value)}
                    disabled={micPermission === 'denied'}
                  >
                    <option value="">System Default</option>
                    {micDevices.map(d => (
                      <option key={d.deviceId} value={d.deviceId}>
                        {d.label || `Microphone (${d.deviceId.slice(0, 8)}…)`}
                      </option>
                    ))}
                  </select>
                  <div className="settings-hint">
                    {micDevices.length > 0
                      ? `${micDevices.length} device(s) found`
                      : micPermission === 'granted' ? 'No devices detected' : 'Grant mic permission to list devices'}
                  </div>
                </div>

                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="settings-test-btn" onClick={loadMicDevices} style={{ flex: 1 }}>
                    ↻ REFRESH
                  </button>
                  <button
                    className={`settings-test-btn ${testingMic ? 'success' : ''}`}
                    onClick={startMicTest}
                    style={{ flex: 2 }}
                    disabled={micPermission === 'denied'}
                  >
                    {testingMic ? '■ STOP TEST' : '▶ TEST MIC'}
                  </button>
                </div>

                {testingMic && (
                  <div style={{ marginTop: 8 }}>
                    <div className="settings-label" style={{ marginBottom: 4 }}>
                      AUDIO LEVEL {micLevel > 5 ? '— hearing you' : '— speak to test'}
                    </div>
                    <div style={{
                      height: 8, borderRadius: 4,
                      background: 'rgba(0,255,255,0.1)',
                      border: '1px solid rgba(0,255,255,0.2)',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        height: '100%',
                        width: `${micLevel}%`,
                        background: micLevel > 60 ? '#00ff88' : micLevel > 20 ? '#00ffff' : '#0088cc',
                        transition: 'width 60ms linear',
                        borderRadius: 4,
                      }} />
                    </div>
                  </div>
                )}
              </div>

              {/* ── Voice / TTS ── */}
              <div className="settings-section">
                <div className="settings-section-label">🎙 VOICE — ELEVENLABS</div>

                <div className="settings-field">
                  <label className="settings-label">API KEY</label>
                  <input className="settings-input" type="password" value={elKey} onChange={e => setElKey(e.target.value)}
                    placeholder="sk_..." spellCheck={false} />
                  <div className="settings-hint">
                    Get a free key at <strong style={{ color: 'var(--cyan-primary)' }}>elevenlabs.io</strong> — 10k chars/month free.
                    Leave empty to use the built-in browser voice.
                  </div>
                </div>

                <div className="settings-field">
                  <label className="settings-label">VOICE</label>
                  <select className="settings-input settings-select" value={voiceId} onChange={e => setVoiceId(e.target.value)}>
                    {ELEVENLABS_VOICES.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
                  </select>
                </div>

                <div className="settings-field">
                  <label className="settings-label">MODEL</label>
                  <select className="settings-input settings-select" value={ttsModel} onChange={e => setTtsModel(e.target.value)}>
                    {ELEVENLABS_MODELS.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                </div>

                <button className={`settings-test-btn ${testingVoice ? '' : ''}`} onClick={testVoice} disabled={testingVoice || !elKey}>
                  {testingVoice && <span className="settings-spinner" />}
                  {testingVoice ? 'PLAYING...' : '▶ TEST VOICE'}
                </button>
                {!elKey && <div className="settings-hint" style={{ color: '#ffaa00' }}>⚠ Add an API key above to test ElevenLabs voice</div>}
              </div>

              {/* ── Local AI ── */}
              <div className="settings-section">
                <div className="settings-section-label">🤖 LOCAL AI — LM STUDIO</div>

                <div className="settings-field">
                  <label className="settings-label">SERVER URL</label>
                  <input className="settings-input" type="text" value={url} onChange={e => { setUrl(e.target.value); setPingStatus(null) }}
                    placeholder="http://192.168.1.108:1234" spellCheck={false} />
                </div>

                <button className={`settings-test-btn ${pingStatus === 'online' ? 'success' : pingStatus === 'offline' ? 'fail' : ''}`}
                  onClick={testConnection} disabled={pingStatus === 'testing'}>
                  {pingStatus === 'testing' && <span className="settings-spinner" />}
                  {pingStatus === 'online' && '✓ '}
                  {pingStatus === 'offline' && '✕ '}
                  {pingStatus === 'testing' ? 'TESTING...' : pingStatus === 'online' ? 'CONNECTED' : pingStatus === 'offline' ? 'UNREACHABLE' : 'TEST CONNECTION'}
                </button>

                <div className="settings-field">
                  <label className="settings-label">MODEL NAME</label>
                  {models.length > 0 ? (
                    <select className="settings-input settings-select" value={model} onChange={e => setModel(e.target.value)}>
                      {models.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  ) : (
                    <input className="settings-input" type="text" value={model} onChange={e => setModel(e.target.value)}
                      placeholder="local-model" spellCheck={false} />
                  )}
                  <div className="settings-hint">
                    {models.length > 0 ? `${models.length} model(s) detected` : 'Test connection to auto-detect'}
                  </div>
                </div>
              </div>

              {/* ── Claude AI info ── */}
              <div className="settings-section">
                <div className="settings-section-label">◆ CLAUDE AI</div>
                <div className="settings-info-box">
                  <div className="settings-info-row">
                    <span className="settings-info-label">API KEY</span>
                    <span className="settings-info-value">
                      {import.meta.env.VITE_ANTHROPIC_API_KEY
                        ? '●●●●' + import.meta.env.VITE_ANTHROPIC_API_KEY.slice(-4)
                        : 'Not configured'}
                    </span>
                  </div>
                  <div className="settings-info-row">
                    <span className="settings-info-label">MODEL</span>
                    <span className="settings-info-value">claude-sonnet-4-20250514</span>
                  </div>
                  <div className="settings-hint" style={{ marginTop: 4 }}>Edit <code>.env</code> to change the API key</div>
                </div>
              </div>

            </div>

            <div className="settings-footer">
              <button className="settings-cancel-btn" onClick={() => setOpen(false)}>CANCEL</button>
              <button className={`settings-save-btn ${saved ? 'saved' : ''}`} onClick={handleSave}>
                {saved ? '✓ SAVED' : 'SAVE CHANGES'}
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
