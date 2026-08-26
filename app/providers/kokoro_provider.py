"""app/providers/kokoro_provider.py — Kokoro local text-to-speech (Model
layer): the Kokoro implementation of the TTSProvider interface in tts.py.

Talks to a locally-running Kokoro-FastAPI server (github.com/remsky/
Kokoro-FastAPI — Kokoro-82M behind an OpenAI-compatible /v1/audio/speech
endpoint, CPU-friendly, no API key, no per-character billing). Configured
entirely by `voice.kokoro_base_url` (e.g. http://localhost:8880): set it and
this provider is live, leave it empty and it's ignored.

British male voice packs (bm_george, bm_lewis, bm_fable) suit the butler;
default matches the ElevenLabs "George" choice.
"""
import json
import urllib.request

from .tts import TTSProvider

DEFAULT_VOICE = "bm_george"


class KokoroTTS(TTSProvider):
    name = "kokoro"

    def is_configured(self):
        return bool((self._cfg.get("voice.kokoro_base_url") or "").strip())

    def synthesize(self, text):
        base_url = self._cfg.get("voice.kokoro_base_url").strip().rstrip("/")
        body = {
            "model": "kokoro",
            "input": text,
            "voice": self._cfg.get("voice.kokoro_voice") or DEFAULT_VOICE,
            "response_format": "mp3",
            "speed": self._cfg.get("voice.kokoro_speed") or 1.0,
        }
        req = urllib.request.Request(
            f"{base_url}/v1/audio/speech",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"content-type": "application/json"},
        )
        # Local CPU inference: quick for short butler replies, but allow slack.
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read(), "audio/mpeg"
