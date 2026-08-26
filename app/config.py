"""app/config.py — JARVIS configuration (Model layer).

Loads config.json deep-merged over config.example.json's defaults, exposes
dotted-path access (cfg.get("voice.elevenlabs_voice_id")), and a
public_dict() that is safe to hand to the browser via GET /config — it must
never include provider_api_key or elevenlabs_api_key.

Reloaded fresh on every request (Config.load()), matching the original
server's per-request load_config() — editing config.json takes effect
without restarting the server.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DEFAULTS_PATH = os.path.join(ROOT, "config.example.json")


def _deep_merge(base, override):
    """Recursively merges `override` onto `base`, returning a new dict.
    Only dict values recurse; lists/scalars are replaced wholesale."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


class Config:
    def __init__(self, data):
        self._data = data

    @classmethod
    def load(cls):
        defaults = _load_json(DEFAULTS_PATH)
        user = _load_json(CONFIG_PATH)
        return cls(_deep_merge(defaults, user))

    def get(self, dotted_path, default=None):
        node = self._data
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # ---- behavior helpers ---------------------------------------------

    def has_real_api_key(self):
        key = self.get("model.provider_api_key", "")
        return bool(key) and "PUT-YOUR-KEY-HERE" not in key and key.strip() != ""

    def public_dict(self):
        """Only what the browser needs — never provider_api_key or
        elevenlabs_api_key. Curated explicitly (not filtered from the full
        dict) so a future secret field can't leak by omission."""
        return {
            "persona": {
                "name": self.get("persona.name", "JARVIS"),
                "address_term": self.get("persona.address_term", "sir"),
            },
            "wake_word": {
                "pattern": self.get("wake_word.pattern", "jarvis"),
                "await_command_ms": self.get("wake_word.await_command_ms", 6000),
                "silence_commit_ms": self.get("wake_word.silence_commit_ms", 1300),
            },
            "conversation": {
                "extra_closing_phrases": self.get("conversation.extra_closing_phrases", []),
                "closing_lines": self.get("conversation.closing_lines", [
                    "Very good, sir. I'll be here when you need me.",
                ]),
            },
            "images": {
                "max_gallery": self.get("images.max_gallery", 6),
            },
            "brain": {
                "radius": self.get("brain.radius", 140),
                "shell_color": self.get("brain.shell_color", "#d4a373"),
                "wire_color": self.get("brain.wire_color", "#f4a261"),
            },
        }

