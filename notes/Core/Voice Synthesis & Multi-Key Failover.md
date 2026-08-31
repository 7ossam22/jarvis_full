# Voice Synthesis & Multi-Key Failover

The **Voice Synthesis & Multi-Key Failover** subsystem powers JARVIS's spoken responses with high-fidelity speech synthesis, key rotation, and seamless failover.

## Voice Backends

1. **ElevenLabs TTS (`ElevenLabsTTS`)**:
   - High-fidelity British butler voice persona ("George" / `JBFqnCBsd6RMkjVDRZzb`).
   - **Multi-Key Pool & Automatic Failover**: Stores multiple ElevenLabs API keys in `config.json` (`elevenlabs_api_keys`). If an active key exceeds its character quota (HTTP 401/429/402), the provider automatically switches to the backup key and continues synthesis without interruptions.
2. **Fish Audio TTS (`FishAudioTTS`)**:
   - Ultra-low latency fallback voice synthesis.
3. **Kokoro TTS (`KokoroTTS`)**:
   - Local offline open-source TTS server fallback.
4. **Browser SpeechSynthesis**:
   - Zero-dependency client-side speech synthesis fallback if all cloud TTS APIs are offline.

## Configuration

Configured in `config.json`:
```json
{
  "voice": {
    "tts_provider": "elevenlabs",
    "elevenlabs_api_keys": [
      "PUT-YOUR-ELEVENLABS-KEY-HERE",
      "PUT-YOUR-BACKUP-ELEVENLABS-KEY-HERE"
    ],
    "elevenlabs_voice_id": "JBFqnCBsd6RMkjVDRZzb"
  }
}
```

## Related Systems

- [[Zen White Glassmorphic UI]] animating the voice spectrum visualizer during speech.
- [[Neural Cortex 3D Graph]] providing spoken memory capture confirmations.
- [[Safety Protocol & Guardrails]] alerting user verbally during override safety prompts.
