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

// ── Constants ──────────────────────────────────────────────────────────────
const WAKE_PHRASES = ['jarvis', 'hey jarvis', 'okay jarvis', 'hello jarvis', 'ok jarvis']

function makeId() {
  return Math.random().toString(36).slice(2)
}

// ── Root with context ──────────────────────────────────────────────────────
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
  const [isListening, setIsListening] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [cameraActive, setCameraActive] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [provider, setProvider] = useState(PROVIDERS.LOCAL)
  const [voiceError, setVoiceError] = useState('')
  const [directMode, setDirectMode] = useState(false)

  const recognitionRef = useRef(null)
  const isListeningRef = useRef(false)
  const isProcessingRef = useRef(false)   // ref so recognition callbacks always see current value
  const micStreamRef = useRef(null)
  const directModeRef = useRef(false)
  const networkErrCountRef = useRef(0)
  const conversationHistoryRef = useRef([])
  const providerRef = useRef(PROVIDERS.LOCAL)
  const sendMessageRef = useRef(null)      // ref so recognition callbacks always see current sendMessage

  const handleProviderChange = useCallback((next) => {
    setProvider(next)
    providerRef.current = next
    // Clear history when switching — context isn't shared between models
    conversationHistoryRef.current = []
    setMessages([])
    const label = next === PROVIDERS.CLAUDE ? 'Claude AI' : 'local AI model'
    speak(`Switching to ${label}, Sir.`, settingsRef.current)
  }, [])

  // ── AI call with history management ──────────────────────────────────────
  const settingsRef = useRef(settings)
  useEffect(() => { settingsRef.current = settings }, [settings])

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

  // ── Local command routing ─────────────────────────────────────────────────
  const handleCommand = useCallback(async (command) => {
    const cmd = command.toLowerCase().trim()

    // ── Spotify media controls ──────────────────────────────────────────────
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

    if (cmd.match(/weather|temperature/)) {
      return "Current weather data is displayed on your left panel, Sir."
    }

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

    if (cmd.match(/^(hello|hi|hey)$/)) {
      return "Good to hear from you, Sir. How may I assist you today?"
    }

    if (cmd.match(/system status|status/)) {
      return `All systems nominal, Sir. Currently routing through ${providerRef.current === PROVIDERS.CLAUDE ? 'Claude AI' : 'your local model'}.`
    }

    // Route to the active AI provider
    return await queryAI(command)
  }, [queryAI, handleProviderChange])

  // ── Send message ──────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (text) => {
    const trimmed = text.trim()
    if (!trimmed || isProcessingRef.current) return  // use ref — never stale

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
  }, [handleCommand])  // removed isProcessing — use ref instead

  // Keep ref in sync so recognition callbacks always call the latest sendMessage
  sendMessageRef.current = sendMessage

  // ── Voice recognition ─────────────────────────────────────────────────────
  const startRecognition = useCallback(async () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      setVoiceError('Speech recognition is not supported in this browser.')
      return
    }

    // Stop any existing recognition to prevent multiple instances competing
    if (recognitionRef.current) {
      try { recognitionRef.current.stop() } catch {}
      recognitionRef.current = null
    }

    // Prime the selected microphone device so recognition uses it
    const deviceId = settingsRef.current?.micDeviceId
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
    }
    try {
      const constraints = { audio: deviceId ? { deviceId: { exact: deviceId } } : true }
      micStreamRef.current = await navigator.mediaDevices.getUserMedia(constraints)
    } catch (err) {
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setVoiceError('Microphone access denied. Allow mic access and try again.')
        return
      }
      if (err.name === 'OverconstrainedError') {
        try { micStreamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true }) } catch {}
      }
    }

    setVoiceError('')
    const recognition = new SpeechRecognition()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = 'en-US'
    recognition.maxAlternatives = 1

    recognition.onstart = () => {
      setIsListening(true)
      setVoiceError('')
      isListeningRef.current = true
    }

    recognition.onend = () => {
      if (isListeningRef.current) {
        try { recognition.start() } catch {}
      } else {
        setIsListening(false)
      }
    }

    recognition.onerror = (event) => {
      const { error } = event
      if (error === 'not-allowed') {
        setVoiceError('Microphone access denied. Allow mic access and try again.')
        isListeningRef.current = false; setIsListening(false)
        directModeRef.current = false;  setDirectMode(false)
      } else if (error === 'network') {
        networkErrCountRef.current += 1
        if (networkErrCountRef.current >= 3)
          setVoiceError('Speech API unreachable — check internet or try a VPN.')
      } else if (error === 'service-not-allowed') {
        setVoiceError('Speech service blocked. Run the app from localhost.')
        isListeningRef.current = false; setIsListening(false)
        directModeRef.current = false;  setDirectMode(false)
      } else if (error === 'audio-capture') {
        setVoiceError('No microphone found. Please connect a microphone.')
        isListeningRef.current = false; setIsListening(false)
        directModeRef.current = false;  setDirectMode(false)
      }
      // no-speech: normal, onend will restart
    }

    recognition.onresult = (event) => {
      // All values read from refs — never stale regardless of when this callback was registered
      networkErrCountRef.current = 0
      setVoiceError('')

      let interimText = ''
      let finalText = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i]
        if (r.isFinal) finalText += r[0].transcript
        else interimText += r[0].transcript
      }

      const currentText = (finalText || interimText).toLowerCase().trim()
      setTranscript(currentText)
      if (!currentText) return

      // ── Direct mode: user clicked mic, no wake word needed ──────────────
      if (directModeRef.current) {
        if (!finalText) return
        setTranscript('')
        directModeRef.current = false
        setDirectMode(false)
        if (!isProcessingRef.current) sendMessageRef.current(finalText.trim())
        return
      }

      // ── Wake word mode ────────────────────────────────────────────────────
      const hasWakeWord = WAKE_PHRASES.some(p => currentText.includes(p))
      if (!hasWakeWord) return

      let command = null
      for (const phrase of [...WAKE_PHRASES].sort((a, b) => b.length - a.length)) {
        if (currentText.includes(phrase)) {
          const after = currentText.split(phrase).pop().trim()
          if (after.length > 1) command = after
          break
        }
      }

      if (!finalText) return
      setTranscript('')

      if (!command) {
        speak("Yes, Sir?", settingsRef.current)
        setMessages(prev => [...prev, {
          id: makeId(), role: 'assistant',
          content: "Yes, Sir? How may I assist you?",
          timestamp: Date.now(),
        }])
        return
      }

      if (!isProcessingRef.current) sendMessageRef.current(command)
    }

    try {
      recognition.start()
      recognitionRef.current = recognition
    } catch (err) {
      setVoiceError(`Could not start recognition: ${err.message}`)
    }
  }, [])  // empty deps — all values read through refs, no stale closures

  const stopRecognition = useCallback(() => {
    isListeningRef.current = false
    directModeRef.current = false
    setIsListening(false)
    setDirectMode(false)
    setTranscript('')
    if (recognitionRef.current) {
      try { recognitionRef.current.stop() } catch {}
      recognitionRef.current = null
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
    }
  }, [])

  const toggleMic = useCallback(() => {
    if (directModeRef.current) {
      // Already in direct mode — cancel it, back to background wake-word listening
      directModeRef.current = false
      setDirectMode(false)
    } else if (isListeningRef.current) {
      // Background listening active — switch to direct mode without restarting recognition
      directModeRef.current = true
      setDirectMode(true)
    } else {
      // Not listening at all — start recognition in direct mode
      directModeRef.current = true
      setDirectMode(true)
      startRecognition()
    }
  }, [startRecognition])

  useEffect(() => {
    if (window.speechSynthesis) window.speechSynthesis.getVoices()
    const t = setTimeout(() => {
      directModeRef.current = false  // background wake-word mode
      startRecognition()
    }, 1200)
    return () => { clearTimeout(t); stopRecognition() }
  }, []) // run once on mount

  return (
    <div className="app-container">
      <div className="background-grid" />
      <div className="ambient-light ambient-light-1" />
      <div className="ambient-light ambient-light-2" />
      <div className="ambient-light ambient-light-3" />

      <TitleBar
        isListening={isListening}
        provider={provider}
        onProviderChange={handleProviderChange}
      />

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
            <CentralOrb
              isListening={isListening}
              isProcessing={isProcessing}
              directMode={directMode}
              onOrbClick={() => {
                if (directModeRef.current) {
                  directModeRef.current = false
                  setDirectMode(false)
                } else if (isListeningRef.current) {
                  directModeRef.current = true
                  setDirectMode(true)
                } else {
                  directModeRef.current = true
                  setDirectMode(true)
                  startRecognition()
                }
              }}
            />
          </div>

          <InputBar
            value={inputText}
            onChange={setInputText}
            onSend={() => sendMessage(inputText)}
            onMicToggle={toggleMic}
            isListening={isListening}
            isProcessing={isProcessing}
            directMode={directMode}
            transcript={transcript}
            voiceError={voiceError}
          />
        </div>

        <div className="right-sidebar">
          <Conversation messages={messages} isProcessing={isProcessing} />
        </div>
      </div>
    </div>
  )
}
