// ── TTS Service ──────────────────────────────────────────────────────────────
// Priority: ElevenLabs (if key configured) → Web Speech API fallback

// Curated voices that suit JARVIS
export const ELEVENLABS_VOICES = [
  { id: 'onwK4e9ZLuTAKqWW03F9', name: 'Daniel — British, Deep (JARVIS)' },
  { id: 'N2lVS1w4EtoT3dr4eOWO', name: 'Callum — Intense, Authoritative' },
  { id: 'ODq5zmih8GrVes37Dizd', name: 'Patrick — Deep, Confident' },
  { id: 'g5CIjZEefAph4nQFvHAz', name: 'Ethan — Natural, Clear' },
  { id: 'VR6AewLTigWG4xSOukaG', name: 'Arnold — Strong, British' },
]

export const ELEVENLABS_MODELS = [
  { id: 'eleven_turbo_v2_5', name: 'Turbo v2.5 (fastest)' },
  { id: 'eleven_turbo_v2',   name: 'Turbo v2' },
  { id: 'eleven_multilingual_v2', name: 'Multilingual v2 (best quality)' },
]

let currentAudio = null

function stopCurrent() {
  if (currentAudio) {
    try { currentAudio.pause(); currentAudio.src = '' } catch {}
    currentAudio = null
  }
  if (window.speechSynthesis) window.speechSynthesis.cancel()
}

// ── ElevenLabs ────────────────────────────────────────────────────────────────
async function speakElevenLabs(text, { elevenLabsKey, voiceId, ttsModel }) {
  const vId = voiceId || ELEVENLABS_VOICES[0].id
  const mId = ttsModel || ELEVENLABS_MODELS[0].id

  // Route through Electron IPC if available (avoids CORS + keeps key in main process)
  if (window.electronAPI?.elevenLabsTts) {
    const result = await window.electronAPI.elevenLabsTts({
      apiKey: elevenLabsKey,
      voiceId: vId,
      text,
      modelId: mId,
    })
    if (!result.ok) throw new Error(result.error)
    // result.audio is base64 mp3
    const binary = atob(result.audio)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    const blob = new Blob([bytes], { type: 'audio/mpeg' })
    const url = URL.createObjectURL(blob)
    return playAudioUrl(url)
  }

  // Browser/web mode: direct fetch
  const res = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${vId}`, {
    method: 'POST',
    headers: {
      'Accept': 'audio/mpeg',
      'Content-Type': 'application/json',
      'xi-api-key': elevenLabsKey,
    },
    body: JSON.stringify({
      text,
      model_id: mId,
      voice_settings: { stability: 0.55, similarity_boost: 0.80, style: 0.2, use_speaker_boost: true },
    }),
  })
  if (!res.ok) throw new Error(`ElevenLabs HTTP ${res.status}`)
  const blob = await res.blob()
  return playAudioUrl(URL.createObjectURL(blob))
}

function playAudioUrl(url) {
  return new Promise((resolve) => {
    const audio = new Audio(url)
    currentAudio = audio
    audio.onended = () => { URL.revokeObjectURL(url); currentAudio = null; resolve() }
    audio.onerror = () => { URL.revokeObjectURL(url); currentAudio = null; resolve() }
    audio.play().catch(() => resolve())
  })
}

// ── Web Speech API fallback ───────────────────────────────────────────────────
function speakWebSpeech(text) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve()

    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 0.9
    utterance.pitch = 0.75
    utterance.volume = 1

    const pickVoice = () => {
      const voices = window.speechSynthesis.getVoices()
      return voices.find(v =>
        v.name.toLowerCase().includes('daniel') ||
        v.name.toLowerCase().includes('british') ||
        v.lang === 'en-GB'
      ) || voices.find(v => v.lang?.startsWith('en') && !v.name.toLowerCase().includes('female'))
    }

    const voice = pickVoice()
    if (voice) utterance.voice = voice

    utterance.onend = resolve
    utterance.onerror = resolve
    window.speechSynthesis.speak(utterance)
  })
}

// ── Public API ────────────────────────────────────────────────────────────────
export function cancelSpeech() {
  stopCurrent()
}

export async function speak(text, settings = {}) {
  stopCurrent()
  if (!text?.trim()) return

  if (settings.elevenLabsKey) {
    try {
      await speakElevenLabs(text, settings)
      return
    } catch (err) {
      console.warn('ElevenLabs TTS failed, falling back to Web Speech:', err.message)
    }
  }

  await speakWebSpeech(text)
}
