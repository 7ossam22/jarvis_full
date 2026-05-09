import React, { useState, useEffect, useRef, useCallback } from 'react'
import './App.css'

import TitleBar from './components/TitleBar'
import CentralOrb from './components/CentralOrb'
import InputBar from './components/InputBar'
import Conversation from './components/Conversation'
import SystemStats from './components/SystemStats'
import Weather from './components/Weather'
import Camera from './components/Camera'
import SystemUptime from './components/SystemUptime'
import SpotifyControls from './components/SpotifyControls'
import { callAI, PROVIDERS } from './services/aiService'
import { speak } from './services/ttsService'
import { SettingsProvider, useSettings } from './context/SettingsContext'
import SettingsModal from './components/SettingsModal'

function makeId() {
  return Math.random().toString(36).slice(2)
}

export default function App() {
  return (
    <SettingsProvider>
      <AppInner />
      <SettingsModal />
    </SettingsProvider>
  )
}

function AppInner() {
  const { settings } = useSettings()
  const [messages, setMessages] = useState([])
  const [inputText, setInputText] = useState('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [cameraActive, setCameraActive] = useState(false)
  const [provider, setProvider] = useState(PROVIDERS.LOCAL)

  const isProcessingRef = useRef(false)
  const conversationHistoryRef = useRef([])
  const providerRef = useRef(PROVIDERS.LOCAL)
  const settingsRef = useRef(settings)
  useEffect(() => { settingsRef.current = settings }, [settings])

  const handleProviderChange = useCallback((next) => {
    setProvider(next)
    providerRef.current = next
    conversationHistoryRef.current = []
    setMessages([])
    const label = next === PROVIDERS.CLAUDE ? 'Claude AI' : 'local AI model'
    speak(`Switching to ${label}, Sir.`, settingsRef.current)
  }, [])

  const queryAI = useCallback(async (userMessage) => {
    conversationHistoryRef.current.push({ role: 'user', content: userMessage })
    try {
      const reply = await callAI(conversationHistoryRef.current, providerRef.current, settingsRef.current)
      conversationHistoryRef.current.push({ role: 'assistant', content: reply })
      return reply
    } catch (err) {
      conversationHistoryRef.current.pop()
      return `I encountered an error, Sir: ${err.message}`
    }
  }, [])

  const handleCommand = useCallback(async (command) => {
    const cmd = command.toLowerCase().trim()

    const spotifyCmd = async (action, reply) => {
      if (window.electronAPI?.spotifyControl) await window.electronAPI.spotifyControl({ action })
      return reply
    }

    if (cmd.match(/\b(next song|next track|skip song|skip track|skip|play next)\b/))
      return spotifyCmd('next', "Next track, Sir.")
    if (cmd.match(/\b(previous song|previous track|last song|go back|play previous|prev)\b/))
      return spotifyCmd('previous', "Previous track, Sir.")
    if (cmd.match(/\b(pause|pause music|pause song|stop music|stop playing)\b/))
      return spotifyCmd('play-pause', "Pausing, Sir.")
    if (cmd.match(/\b(resume|resume music|play music|unpause)\b/))
      return spotifyCmd('play-pause', "Resuming playback, Sir.")
    if (cmd.match(/\b(volume up|louder|increase volume|turn it up|turn up)\b/))
      return spotifyCmd('volume-up', "Volume up, Sir.")
    if (cmd.match(/\b(volume down|quieter|lower volume|decrease volume|turn it down|turn down)\b/))
      return spotifyCmd('volume-down', "Volume down, Sir.")
    if (cmd.match(/\b(mute|silence music|mute music)\b/))
      return spotifyCmd('mute', "Muted, Sir.")

    if (cmd.includes('open spotify') || cmd.includes('play spotify') || cmd.includes('launch spotify')) {
      if (window.electronAPI) {
        const result = await window.electronAPI.openSpotify()
        return result.success
          ? "Opening Spotify for you, Sir."
          : "Unable to launch Spotify, Sir. Please ensure it is installed."
      }
      return "Spotify control is only available in the desktop application, Sir."
    }

    if (cmd.includes('search for ') || cmd.match(/^(google|search)\s+.+/i)) {
      const query = cmd.replace(/^(search for|google|search)\s+/i, '').trim()
      if (query) {
        if (window.electronAPI) await window.electronAPI.googleSearch(query)
        else window.open(`https://www.google.com/search?q=${encodeURIComponent(query)}`, '_blank')
        return `Searching Google for "${query}", Sir.`
      }
    }

    if (cmd.match(/turn on camera|enable camera|activate camera/)) {
      setCameraActive(true)
      return "Camera activated, Sir. Visual feed is now online."
    }
    if (cmd.match(/turn off camera|disable camera|deactivate camera/)) {
      setCameraActive(false)
      return "Camera deactivated, Sir."
    }

    if (cmd.match(/weather|temperature/))
      return "Current weather data is displayed on your left panel, Sir."

    if (cmd.match(/^(open|launch) youtube$/)) {
      const url = 'https://www.youtube.com'
      if (window.electronAPI) await window.electronAPI.openUrl(url)
      else window.open(url, '_blank')
      return "Opening YouTube, Sir."
    }

    if (cmd.match(/^(open|launch) github$/)) {
      const url = 'https://github.com'
      if (window.electronAPI) await window.electronAPI.openUrl(url)
      else window.open(url, '_blank')
      return "Opening GitHub, Sir."
    }

    if (cmd.match(/^(switch to|use) (claude|anthropic)/i)) {
      handleProviderChange(PROVIDERS.CLAUDE)
      return "Switched to Claude AI, Sir."
    }
    if (cmd.match(/^(switch to|use) (local|lm studio|local ai|local model)/i)) {
      handleProviderChange(PROVIDERS.LOCAL)
      return "Switched to your local AI model, Sir."
    }

    if (cmd.match(/^(hello|hi|hey)$/))
      return "Good to hear from you, Sir. How may I assist you today?"

    if (cmd.match(/system status|status/))
      return `All systems nominal, Sir. Currently routing through ${providerRef.current === PROVIDERS.CLAUDE ? 'Claude AI' : 'your local model'}.`

    return await queryAI(command)
  }, [queryAI, handleProviderChange])

  const sendMessage = useCallback(async (text) => {
    const trimmed = text.trim()
    if (!trimmed || isProcessingRef.current) return

    setMessages(prev => [...prev, { id: makeId(), role: 'user', content: trimmed, timestamp: Date.now() }])
    setInputText('')
    isProcessingRef.current = true
    setIsProcessing(true)

    try {
      const reply = await handleCommand(trimmed)
      setMessages(prev => [...prev, { id: makeId(), role: 'assistant', content: reply, timestamp: Date.now() }])
      speak(reply, settingsRef.current)
    } finally {
      isProcessingRef.current = false
      setIsProcessing(false)
    }
  }, [handleCommand])

  return (
    <div className="app-container">
      <div className="background-grid" />
      <div className="ambient-light ambient-light-1" />
      <div className="ambient-light ambient-light-2" />
      <div className="ambient-light ambient-light-3" />

      <TitleBar provider={provider} onProviderChange={handleProviderChange} />

      <div className="main-content">
        <div className="left-sidebar">
          <SystemStats />
          <Weather />
          <SpotifyControls />
          <Camera active={cameraActive} onToggle={() => setCameraActive(v => !v)} />
          <SystemUptime />
        </div>

        <div className="center-column">
          <div className="orb-container">
            <CentralOrb isProcessing={isProcessing} />
          </div>

          <InputBar
            value={inputText}
            onChange={setInputText}
            onSend={() => sendMessage(inputText)}
            isProcessing={isProcessing}
          />
        </div>

        <div className="right-sidebar">
          <Conversation messages={messages} isProcessing={isProcessing} />
        </div>
      </div>
    </div>
  )
}
