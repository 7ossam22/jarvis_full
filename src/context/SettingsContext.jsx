import React, { createContext, useContext, useState, useCallback } from 'react'

const STORAGE_KEY = 'jarvis_settings'

const DEFAULTS = {
  localAiUrl: 'http://192.168.1.108:1234',
  localAiModel: 'local-model',
  elevenLabsKey: '',
  voiceId: 'onwK4e9ZLuTAKqWW03F9',
  ttsModel: 'eleven_turbo_v2_5',
  micDeviceId: '',
}

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) }
  } catch {}
  return { ...DEFAULTS }
}

const SettingsContext = createContext(null)

export function SettingsProvider({ children }) {
  const [settings, setSettings] = useState(load)
  const [open, setOpen] = useState(false)

  const save = useCallback((next) => {
    const merged = { ...settings, ...next }
    setSettings(merged)
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(merged)) } catch {}
  }, [settings])

  return (
    <SettingsContext.Provider value={{ settings, save, open, setOpen }}>
      {children}
    </SettingsContext.Provider>
  )
}

export function useSettings() {
  return useContext(SettingsContext)
}
