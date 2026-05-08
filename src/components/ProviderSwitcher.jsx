import React, { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { PROVIDERS, pingLocalAI } from '../services/aiService'
import { useSettings } from '../context/SettingsContext'

export default function ProviderSwitcher({ provider, onChange }) {
  const { settings } = useSettings()
  const [localStatus, setLocalStatus] = useState('unknown') // 'online' | 'offline' | 'unknown'
  const [localModel, setLocalModel] = useState('')

  useEffect(() => {
    const check = async () => {
      const result = await pingLocalAI(settings.localAiUrl)
      setLocalStatus(result.online ? 'online' : 'offline')
      if (result.models.length > 0) {
        // Show just the model filename without path
        const name = result.models[0].split('/').pop().split('\\').pop()
        setLocalModel(name.length > 22 ? name.slice(0, 22) + '…' : name)
      } else {
        setLocalModel('')
      }
    }

    check()
    const interval = setInterval(check, 15000)
    return () => clearInterval(interval)
  }, [settings.localAiUrl])

  const isClaude = provider === PROVIDERS.CLAUDE
  const isLocal = provider === PROVIDERS.LOCAL

  return (
    <div className="provider-switcher">
      <button
        className={`provider-btn ${isClaude ? 'active' : ''}`}
        onClick={() => onChange(PROVIDERS.CLAUDE)}
        title="Use Claude (Anthropic)"
      >
        <span className="provider-icon">◆</span>
        <span className="provider-name">CLAUDE</span>
        {isClaude && (
          <motion.span
            className="provider-active-dot"
            layoutId="active-dot"
            style={{ background: '#00d4ff' }}
          />
        )}
      </button>

      <div className="provider-divider" />

      <button
        className={`provider-btn ${isLocal ? 'active' : ''} ${localStatus === 'offline' ? 'degraded' : ''}`}
        onClick={() => onChange(PROVIDERS.LOCAL)}
        title={`Local AI via LM Studio (${localStatus})`}
      >
        <span className="provider-icon">⬡</span>
        <span className="provider-name">LOCAL</span>
        <span
          className="provider-status-dot"
          style={{
            background: localStatus === 'online' ? '#00ff88' : localStatus === 'offline' ? '#ff4444' : '#ffaa00',
            boxShadow: `0 0 6px ${localStatus === 'online' ? '#00ff88' : localStatus === 'offline' ? '#ff4444' : '#ffaa00'}`,
          }}
        />
        {isLocal && (
          <motion.span
            className="provider-active-dot"
            layoutId="active-dot"
            style={{ background: '#00ff88' }}
          />
        )}
      </button>

      <AnimatePresence>
        {isLocal && localModel && (
          <motion.div
            className="provider-model-tag"
            initial={{ opacity: 0, width: 0 }}
            animate={{ opacity: 1, width: 'auto' }}
            exit={{ opacity: 0, width: 0 }}
            transition={{ duration: 0.25 }}
          >
            {localModel}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
