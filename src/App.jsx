import React, { useState, useEffect, useRef, useCallback } from 'react'
import './App.css'

import TitleBar from './components/TitleBar'
import CentralOrb from './components/CentralOrb'
import InputBar from './components/InputBar'
import Conversation from './components/Conversation'
import SystemStats from './components/SystemStats'
import Weather from './components/Weather'
import CameraOverlay from './components/Camera'
import { callAI, PROVIDERS } from './services/aiService'
import { speak } from './services/ttsService'
import { logger } from './services/logger'
import LogViewer from './components/LogViewer'
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
  const [logsVisible, setLogsVisible] = useState(false)

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
    logger.info('AI', `Sending to AI: ${userMessage.slice(0, 80)}`)
    conversationHistoryRef.current.push({ role: 'user', content: userMessage })
    try {
      const reply = await callAI(conversationHistoryRef.current, providerRef.current, settingsRef.current)
      conversationHistoryRef.current.push({ role: 'assistant', content: reply })
      logger.info('AI', `AI replied: ${reply.slice(0, 80)}`)
      return reply
    } catch (err) {
      conversationHistoryRef.current.pop()
      logger.error('AI', `AI error: ${err.message}`)
      return `I encountered an error, Sir: ${err.message}`
    }
  }, [])

  const handleCommand = useCallback(async (command) => {
    logger.info('CMD', `Received command: "${command}"`)
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
    if (cmd.match(/\b(resume|resume music|unpause)\b/))
      return spotifyCmd('play-pause', "Resuming playback, Sir.")
    if (cmd.match(/\b(volume up|louder|increase volume|turn it up|turn up)\b/))
      return spotifyCmd('volume-up', "Volume up, Sir.")
    if (cmd.match(/\b(volume down|quieter|lower volume|decrease volume|turn it down|turn down)\b/))
      return spotifyCmd('volume-down', "Volume down, Sir.")
    if (cmd.match(/\b(mute|silence music|mute music)\b/))
      return spotifyCmd('mute', "Muted, Sir.")

    // "play music" / "play some music" / "play a song" — sends play-pause if spotify is open, or opens Spotify
    if (cmd.match(/\b(play music|play some music|play a song|play something|start music|start playing)\b/)) {
      if (window.electronAPI?.spotifyControl) {
        await window.electronAPI.spotifyControl({ action: 'play-pause' })
      }
      return "Playing music, Sir."
    }

    // "close spotify" / "quit spotify" / "exit spotify" / "kill spotify"
    if (cmd.match(/\b(close spotify|quit spotify|exit spotify|kill spotify|stop spotify|shut down spotify|close music app)\b/)) {
      if (window.electronAPI?.closeSpotify) {
        const result = await window.electronAPI.closeSpotify()
        return result.success ? "Spotify closed, Sir." : "Unable to close Spotify, Sir."
      }
      return "Spotify close is only available in the desktop app, Sir."
    }

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

    if (cmd.match(/\b(open camera|look at me|what is this|watch me|show camera|turn on camera|enable camera|activate camera|camera on|start camera)\b/)) {
      setCameraActive(true)
      return "Camera activated, Sir. Visual feed is now online."
    }
    if (cmd.match(/\b(close camera|stop looking|am done|i('m| am) done|done|dismiss camera|turn off camera|disable camera|deactivate camera|camera off)\b/)) {
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

    if (cmd.match(/\b(show logs|open logs|show debug|debug mode|show log viewer|display logs)\b/)) {
      setLogsVisible(true)
      return "Debug log viewer opened, Sir."
    }
    if (cmd.match(/\b(hide logs|close logs|hide debug|close debug|dismiss logs)\b/)) {
      setLogsVisible(false)
      return "Log viewer closed, Sir."
    }

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
      logger.debug('INPUT', `Processing message: "${trimmed}"`)
      const reply = await handleCommand(trimmed)
      setMessages(prev => [...prev, { id: makeId(), role: 'assistant', content: reply, timestamp: Date.now() }])
      speak(reply, settingsRef.current)
    } finally {
      isProcessingRef.current = false
      setIsProcessing(false)
      logger.debug('INPUT', 'Message processing complete')
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
        </div>

        <div className="center-column">
          <div className="orb-container">
            <CentralOrb isProcessing={isProcessing} />
          </div>

          <InputBar
            value={inputText}
            onChange={setInputText}
            onSend={sendMessage}
            isProcessing={isProcessing}
          />
        </div>

        <div className="right-sidebar">
          <Conversation messages={messages} isProcessing={isProcessing} />
        </div>
      </div>
      <CameraOverlay active={cameraActive} onClose={() => setCameraActive(false)} />
      <LogViewer visible={logsVisible} onClose={() => setLogsVisible(false)} />
    </div>
  )
}
