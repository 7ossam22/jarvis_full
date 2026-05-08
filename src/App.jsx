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
import { loadModel, transcribeBlob, isLoaded } from './services/sttService'
import { VAD } from './services/vadService'
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
  const [modelLoading, setModelLoading] = useState(false)
  const [modelProgress, setModelProgress] = useState(0)

  const isListeningRef = useRef(false)
  const isProcessingRef = useRef(false)
  const micStreamRef = useRef(null)
  const vadRef = useRef(null)
  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const directModeRef = useRef(false)
  const modelLoadedRef = useRef(false)
  const conversationHistoryRef = useRef([])
  const providerRef = useRef(PROVIDERS.LOCAL)
  const sendMessageRef = useRef(null)

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

  // ── Process a Whisper transcript ──────────────────────────────────────────
  const handleTranscriptRef = useRef(null)
  handleTranscriptRef.current = (rawText) => {
    const text = rawText.trim()
    if (!text) return
    const lower = text.toLowerCase()
    setTranscript(lower)

    if (directModeRef.current) {
      setTranscript('')
      directModeRef.current = false
      setDirectMode(false)
      if (!isProcessingRef.current) sendMessageRef.current(text)
      return
    }

    const hasWakeWord = WAKE_PHRASES.some(p => lower.includes(p))
    if (!hasWakeWord) { setTimeout(() => setTranscript(''), 2500); return }

    let command = null
    for (const phrase of [...WAKE_PHRASES].sort((a, b) => b.length - a.length)) {
      if (lower.includes(phrase)) {
        const after = lower.split(phrase).pop().trim()
        if (after.length > 1) command = after
        break
      }
    }

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

  // ── Local Whisper voice recognition (no Google, no internet required) ────
  const stopRecognition = useCallback(() => {
    if (vadRef.current) { vadRef.current.destroy(); vadRef.current = null }
    if (recorderRef.current?.state === 'recording') {
      try { recorderRef.current.stop() } catch {}
      recorderRef.current = null
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
    }
    isListeningRef.current = false
    directModeRef.current = false
    setIsListening(false)
    setDirectMode(false)
    setTranscript('')
  }, [])

  const startRecognition = useCallback(async () => {
    if (!modelLoadedRef.current) {
      setVoiceError('Voice model still loading — please wait.')
      return
    }

    stopRecognition()

    const deviceId = settingsRef.current?.micDeviceId
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      })
    } catch (err) {
      setVoiceError(
        err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'
          ? 'Microphone access denied. Allow mic access and try again.'
          : `Microphone error: ${err.message}`
      )
      return
    }
    micStreamRef.current = stream

    const processBlob = async (blob) => {
      if (blob.size < 4000) return  // skip clips too short to be real speech
      isProcessingRef.current = true
      setIsProcessing(true)
      try {
        const text = await transcribeBlob(blob)
        if (text) handleTranscriptRef.current(text)
      } catch (err) {
        console.error('Whisper STT error:', err)
      } finally {
        isProcessingRef.current = false
        setIsProcessing(false)
      }
    }

    const vad = new VAD({
      threshold: 10,
      silenceMs: 1300,
      onSpeechStart: () => {
        chunksRef.current = []
        const mr = new MediaRecorder(stream)
        mr.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data) }
        mr.onstop = () => {
          const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
          chunksRef.current = []
          processBlob(blob)
        }
        mr.start(100)
        recorderRef.current = mr
      },
      onSpeechEnd: () => {
        if (recorderRef.current?.state === 'recording') {
          recorderRef.current.stop()
          recorderRef.current = null
        }
      },
    })

    vad.start(stream)
    vadRef.current = vad
    isListeningRef.current = true
    setIsListening(true)
    setVoiceError('')
  }, [stopRecognition])

  const toggleMic = useCallback(() => {
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
  }, [startRecognition])

  // Load Whisper model on mount, then auto-start listening
  useEffect(() => {
    if (window.speechSynthesis) window.speechSynthesis.getVoices()
    setModelLoading(true)
    loadModel(p => {
      if (p.status === 'progress' || p.status === 'downloading') {
        setModelProgress(Math.round(p.progress ?? 0))
      }
    }).then(() => {
      modelLoadedRef.current = true
      setModelLoading(false)
      setModelProgress(0)
      startRecognition()
    }).catch(err => {
      setModelLoading(false)
      setVoiceError(`Voice model failed to load: ${err.message}`)
    })
    return () => stopRecognition()
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
              modelLoading={modelLoading}
              modelProgress={modelProgress}
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
