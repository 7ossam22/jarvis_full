"""app/providers/elevenlabs_provider.py — ElevenLabs text-to-speech (Model
layer). Moved verbatim from server.py."""
import json
import urllib.request

DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British male
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"


def call_elevenlabs_tts(cfg, text):
    """Returns (audio_bytes, content_type). Raises on any failure — caller falls
    back to the browser's own speechSynthesis, so this is never a hard dependency."""
    api_key = cfg.get("voice.elevenlabs_api_key")
    voice_id = cfg.get("voice.elevenlabs_voice_id") or DEFAULT_VOICE_ID
    model_id = cfg.get("voice.elevenlabs_model_id") or DEFAULT_MODEL_ID
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
