"""app/providers/elevenlabs_provider.py — ElevenLabs text-to-speech (Model
layer): the ElevenLabs implementation of the TTSProvider interface in tts.py.

Supports multi-key pool with automatic failover (rotating to backup keys when
a key runs out of quota, hits rate limits, or fails).
"""
import json
import sys
import urllib.error
import urllib.request

from .tts import TTSProvider

DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British male
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"

_active_key_index = 0


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def _get_keys(self):
        """Returns a list of all configured ElevenLabs API keys."""
        keys = []
        raw_list = self._cfg.get("voice.elevenlabs_api_keys")
        if isinstance(raw_list, list):
            for k in raw_list:
                if isinstance(k, str) and k.strip() and "PUT-YOUR" not in k:
                    if k.strip() not in keys:
                        keys.append(k.strip())

        raw_single = self._cfg.get("voice.elevenlabs_api_key")
        if isinstance(raw_single, str) and raw_single.strip() and "PUT-YOUR" not in raw_single:
            for part in raw_single.split(","):
                part_clean = part.strip()
                if part_clean and part_clean not in keys:
                    keys.append(part_clean)

        return keys

    def is_configured(self):
        return len(self._get_keys()) > 0

    def synthesize(self, text):
        global _active_key_index
        keys = self._get_keys()
        if not keys:
            raise ValueError("No valid ElevenLabs API keys configured.")

        voice_id = self._cfg.get("voice.elevenlabs_voice_id") or DEFAULT_VOICE_ID
        model_id = self._cfg.get("voice.elevenlabs_model_id") or DEFAULT_MODEL_ID
        payload = json.dumps({
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode("utf-8")

        num_keys = len(keys)
        last_error = None

        # Start from the current active key and try all keys in the pool if needed
        for attempt in range(num_keys):
            idx = (_active_key_index + attempt) % num_keys
            api_key = keys[idx]
            masked_key = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"

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

            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = resp.read()
                    # On success, lock in this working key as primary active key
                    _active_key_index = idx
                    return data, "audio/mpeg"
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
                err_detail = ""
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        err_detail = f" (HTTP {e.code}: {e.read().decode('utf-8')[:200]})"
                    except Exception:
                        err_detail = f" (HTTP {e.code})"
                print(f"[jarvis] ElevenLabs key [{masked_key}] failed{err_detail}; trying backup key…", file=sys.stderr)
                last_error = e

        if last_error:
            raise last_error
        raise RuntimeError("All ElevenLabs API keys in the pool failed.")
