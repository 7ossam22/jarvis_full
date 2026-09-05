"""app/providers/tts.py — TTS provider interface & selection (Model layer).

TTSProvider is the interface every speech backend implements (ElevenLabs,
Fish Audio); get_tts_provider() is the dependency-injection point: it reads
`voice.tts_provider` from config and hands the caller whichever configured
implementation was chosen. Callers (app/controllers.py's handle_speak) only
ever talk to the interface — adding another voice service means one new
subclass and one line in the registry here, nothing else changes.
"""
from abc import ABC, abstractmethod


class TTSProvider(ABC):
    """One text-to-speech backend. Constructed with the Config it reads keys from."""

    name = "abstract"  # the `voice.tts_provider` value that selects this backend

    def __init__(self, cfg):
        self._cfg = cfg

    @abstractmethod
    def is_configured(self):
        """True when this backend has a usable API key in config."""

    @abstractmethod
    def synthesize(self, text):
        """Returns (audio_bytes, content_type). Raises on any failure — the
        caller falls back to the browser's own speechSynthesis, so no backend
        is ever a hard dependency."""


def get_tts_providers(cfg):
    """Returns the usable TTSProvider instances in the order they should be
    tried: the one `voice.tts_provider` ("elevenlabs" | "fish_audio" |
    "kokoro") names first, then any other backend with a real key as failover
    — a provider outage (or an unpaid account) degrades to the next real
    voice, not straight to the browser's robot voice. With no explicit choice
    the order is ElevenLabs first, for backward compatibility.

    Set `voice.tts_failover` to false to drop that safety net and use only the
    chosen backend. That is what you want once the choice is local Kokoro:
    failing over from a free voice on this machine to a metered cloud one
    would spend money precisely when you didn't ask it to."""
    from .elevenlabs_provider import ElevenLabsTTS
    from .fish_audio_provider import FishAudioTTS
    from .kokoro_provider import KokoroTTS

    ordered = [p for p in (ElevenLabsTTS(cfg), FishAudioTTS(cfg), KokoroTTS(cfg))
               if p.is_configured()]

    choice = (cfg.get("voice.tts_provider") or "").strip().lower()
    ordered.sort(key=lambda p: p.name != choice)  # stable: chosen one first

    failover = cfg.get("voice.tts_failover")
    if failover is False and choice:
        # Keep only the chosen backend — but if it is not configured at all,
        # an empty list would silence the voice entirely, so fall back to the
        # full order rather than pretending nothing is available.
        only = [p for p in ordered if p.name == choice]
        if only:
            return only
    return ordered
