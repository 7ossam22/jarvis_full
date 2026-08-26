"""app/providers/elevenlabs_provider.py — ElevenLabs text-to-speech (Model
layer): the ElevenLabs implementation of the TTSProvider interface in tts.py."""
import json
import urllib.request

from .tts import TTSProvider

DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British male
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def is_configured(self):
        key = self._cfg.get("voice.elevenlabs_api_key") or ""
        return bool(key.strip()) and "PUT-YOUR" not in key

    def synthesize(self, text):
        api_key = self._cfg.get("voice.elevenlabs_api_key")
        voice_id = self._cfg.get("voice.elevenlabs_voice_id") or DEFAULT_VOICE_ID
        model_id = self._cfg.get("voice.elevenlabs_model_id") or DEFAULT_MODEL_ID
        payload = json.dumps({
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            data=payload,
            method="POST",
            headers={
                "content-type": "application/json",
                "xi-api-key": api_key,
                "accept": "audio/mpeg",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(), "audio/mpeg"
