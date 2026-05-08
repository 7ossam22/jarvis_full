import React, { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useSettings } from '../context/SettingsContext'
import { pingLocalAI } from '../services/aiService'

export default function SettingsModal() {
  const { settings, save, open, setOpen } = useSettings()

  const [url, setUrl] = useState(settings.localAiUrl)
  const [model, setModel] = useState(settings.localAiModel)
  const [pingStatus, setPingStatus] = useState(null) // null | 'testing' | 'online' | 'offline'
  const [models, setModels] = useState([])
  const [saved, setSaved] = useState(false)

  // Sync local state when modal opens
  useEffect(() => {
    if (open) {
      setUrl(settings.localAiUrl)
      setModel(settings.localAiModel)
      setPingStatus(null)
      setModels([])
      setSaved(false)
    }
  }, [open, settings])

  const testConnection = async () => {
    setPingStatus('testing')
    setModels([])
    const result = await pingLocalAI(url)
    setPingStatus(result.online ? 'online' : 'offline')
    if (result.models.length > 0) {
      setModels(result.models)
      // Auto-fill model if only one is loaded
      if (result.models.length === 1) setModel(result.models[0])
    }
  }

  const handleSave = () => {
    save({ localAiUrl: url.trim(), localAiModel: model.trim() })
    setSaved(true)
    setTimeout(() => { setSaved(false); setOpen(false) }, 800)
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            className="settings-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          />

          {/* Panel */}
          <motion.div
            className="settings-panel"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          >
            {/* Header */}
            <div className="settings-header">
              <div>
                <div className="settings-title">⚙ SETTINGS</div>
                <div className="settings-subtitle">SYSTEM CONFIGURATION</div>
              </div>
              <button className="settings-close" onClick={() => setOpen(false)}>✕</button>
            </div>

            <div className="settings-body">

              {/* Local AI section */}
              <div className="settings-section">
                <div className="settings-section-label">LOCAL AI — LM STUDIO</div>

                <div className="settings-field">
                  <label className="settings-label">SERVER URL</label>
                  <input
                    className="settings-input"
                    type="text"
                    value={url}
                    onChange={e => { setUrl(e.target.value); setPingStatus(null) }}
                    placeholder="http://192.168.1.108:1234"
                    spellCheck={false}
                  />
                  <div className="settings-hint">IP address and port of your LM Studio instance</div>
                </div>

                <button
                  className={`settings-test-btn ${pingStatus === 'online' ? 'success' : pingStatus === 'offline' ? 'fail' : ''}`}
                  onClick={testConnection}
                  disabled={pingStatus === 'testing'}
                >
                  {pingStatus === 'testing' && <span className="settings-spinner" />}
                  {pingStatus === 'online' && '✓ '}
                  {pingStatus === 'offline' && '✕ '}
                  {pingStatus === 'testing' ? 'TESTING...' :
                   pingStatus === 'online' ? 'CONNECTED' :
                   pingStatus === 'offline' ? 'UNREACHABLE' :
                   'TEST CONNECTION'}
                </button>

                <div className="settings-field" style={{ marginTop: 14 }}>
                  <label className="settings-label">MODEL NAME</label>
                  {models.length > 0 ? (
                    <select
                      className="settings-input settings-select"
                      value={model}
                      onChange={e => setModel(e.target.value)}
                    >
                      {models.map(m => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="settings-input"
                      type="text"
                      value={model}
                      onChange={e => setModel(e.target.value)}
                      placeholder="local-model"
                      spellCheck={false}
                    />
                  )}
                  <div className="settings-hint">
                    {models.length > 0
                      ? `${models.length} model(s) detected — select one`
                      : 'Test connection to auto-detect loaded models'}
                  </div>
                </div>
              </div>

              {/* Info section */}
              <div className="settings-section">
                <div className="settings-section-label">CLAUDE AI</div>
                <div className="settings-info-box">
                  <div className="settings-info-row">
                    <span className="settings-info-label">API KEY</span>
                    <span className="settings-info-value">
                      {import.meta.env.VITE_ANTHROPIC_API_KEY
                        ? '●●●●●●●●' + import.meta.env.VITE_ANTHROPIC_API_KEY.slice(-4)
                        : 'Not configured'}
                    </span>
                  </div>
                  <div className="settings-info-row">
                    <span className="settings-info-label">MODEL</span>
                    <span className="settings-info-value">claude-sonnet-4-20250514</span>
                  </div>
                  <div className="settings-hint" style={{ marginTop: 6 }}>
                    Edit <code>.env</code> to change the API key
                  </div>
                </div>
              </div>

            </div>

            {/* Footer */}
            <div className="settings-footer">
              <button className="settings-cancel-btn" onClick={() => setOpen(false)}>
                CANCEL
              </button>
              <button
                className={`settings-save-btn ${saved ? 'saved' : ''}`}
                onClick={handleSave}
              >
                {saved ? '✓ SAVED' : 'SAVE CHANGES'}
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
