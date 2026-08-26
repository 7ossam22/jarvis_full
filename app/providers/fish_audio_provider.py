"""app/providers/fish_audio_provider.py — Fish Audio text-to-speech (Model
layer): the Fish Audio implementation of the TTSProvider interface in tts.py.

Uses the plain-JSON variant of Fish Audio's /v1/tts endpoint (it also speaks
msgpack, but JSON keeps this standard-library only). The voice is picked via
`voice.fish_audio_reference_id` (a voice model ID from fish.audio's
playground); without one, Fish Audio uses its default voice. The generation
model (s1, speech-1.6, …) is selected with the `model` header via
`voice.fish_audio_model`.
"""
import json
import urllib.request

from .tts import TTSProvider

DEFAULT_MODEL = "s1"
DEFAULT_BASE_URL = "https://api.fish.audio"


class FishAudioTTS(TTSProvider):
    name = "fish_audio"

    def _base_url(self):
        return (self._cfg.get("voice.fish_audio_base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _is_local(self):
        # A self-hosted fish-speech server (speech.fish.audio) speaks the same
        # /v1/tts API but needs no API key.
        return self._base_url() != DEFAULT_BASE_URL

    def is_configured(self):
        if self._is_local():
            return True
        key = self._cfg.get("voice.fish_audio_api_key") or ""
        return bool(key.strip()) and "PUT-YOUR" not in key

    def synthesize(self, text):
        model = self._cfg.get("voice.fish_audio_model") or DEFAULT_MODEL
        body = {"text": text, "format": "mp3"}
        reference_id = self._cfg.get("voice.fish_audio_reference_id")
        if reference_id:
            body["reference_id"] = reference_id
        headers = {"content-type": "application/json", "model": model}
        api_key = self._cfg.get("voice.fish_audio_api_key")
        if api_key and "PUT-YOUR" not in api_key:
            headers["authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            f"{self._base_url()}/v1/tts",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        # A local model on modest hardware can take far longer than the cloud.
        timeout = 120 if self._is_local() else 30
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), "audio/mpeg"
