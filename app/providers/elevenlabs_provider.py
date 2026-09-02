"""app/providers/elevenlabs_provider.py — ElevenLabs text-to-speech (Model
layer): the ElevenLabs implementation of the TTSProvider interface in tts.py.

Supports multi-key pool with automatic failover (rotating to backup keys when
a key runs out of quota, hits rate limits, or fails).
"""
import json
import sys
import time
import urllib.error
import urllib.request

from .tts import TTSProvider

DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British male
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"

_active_key_index = 0

# A key that is out of credits will be out of credits for the rest of the
# billing period — retrying it is a guaranteed failed round-trip. Without this,
# every single utterance re-tried all five exhausted keys before giving up:
# five pointless HTTPS calls and five alarming log lines per spoken sentence,
# repeated forever. Remembered ones are skipped until the cooldown lapses,
# which is short enough that topping up an account is picked up on its own.
_exhausted_until = {}
_QUOTA_COOLDOWN_S = 30 * 60


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
        now = time.monotonic()
        skipped = 0

        # Start from the current active key and try all keys in the pool if needed
        for attempt in range(num_keys):
            idx = (_active_key_index + attempt) % num_keys
            api_key = keys[idx]
            masked_key = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"

            if _exhausted_until.get(api_key, 0) > now:
                skipped += 1
                continue

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
                err_detail, out_of_credits = "", False
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        raw = e.read().decode("utf-8")[:300]
                        out_of_credits = "quota_exceeded" in raw
                        err_detail = f" (HTTP {e.code}: {raw[:200]})"
                    except Exception:
                        err_detail = f" (HTTP {e.code})"
                if out_of_credits:
                    # Name the actual cause. "failed; trying backup key" reads
                    # like a transient blip, which is how an account that has
                    # simply run out of credits looks like a bug for weeks.
                    _exhausted_until[api_key] = now + _QUOTA_COOLDOWN_S
                    print(f"[jarvis] ElevenLabs key [{masked_key}] is OUT OF CREDITS; "
                          f"skipping it for {_QUOTA_COOLDOWN_S // 60} min.", file=sys.stderr)
                else:
                    print(f"[jarvis] ElevenLabs key [{masked_key}] failed{err_detail}; trying backup key…", file=sys.stderr)
                last_error = e

        if skipped == num_keys:
            raise RuntimeError(
                f"All {num_keys} ElevenLabs keys are out of credits — top up the account "
                f"or add a working key to voice.elevenlabs_api_keys. (Speech is off; text "
                f"answers are unaffected.)")
        if last_error:
            raise last_error
        raise RuntimeError("All ElevenLabs API keys in the pool failed.")
