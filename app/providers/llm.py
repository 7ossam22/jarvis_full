"""app/providers/llm.py — LLM provider interface & selection (Model layer).

LLMProvider is the interface every model backend implements; get_llm_providers()
/call_model() are the dependency-injection point — the exact same pattern as
providers/tts.py's TTSProvider: callers only ever talk to the interface,
`model.provider` in config picks the backend explicitly, and any other
configured backend serves as automatic failover.

Available providers (app/providers/*_provider.py):
  - "anthropic" (anthropic_provider.py) — Claude via the direct API
    (model.provider_api_key + model.model_id), falling back to the local
    `claude -p` CLI (a logged-in Claude Code subscription) when no API key
    is set or the API call fails. is_configured() is true if EITHER exists.
  - "gemini" (gemini_provider.py) — Google Gemini via the direct API
    (model.gemini_api_key + model.gemini_model_id). No local CLI fallback.
  - "lmstudio" (lmstudio_provider.py) — a local model on the LAN served by
    LM Studio's OpenAI-compatible server (model.lmstudio_base_url, e.g.
    http://192.168.1.50:1234/v1). No web-search tool; connector tools work.

How to switch, in config.json:
    "model": { "provider": "gemini" }     # or "anthropic" / "lmstudio"
Leave "provider" empty/unset and Anthropic is tried first (backward-compatible
default) with Gemini as silent failover — or vice versa, whichever backend
IS configured if only one has a real key. Either way, an unconfigured
provider (no key, no CLI) is simply skipped, never a hard error — see
get_llm_providers() below.
"""
import sys
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """One LLM backend. Constructed with the Config it reads keys from."""

    name = "abstract"  # the `model.provider` value that selects this backend

    def __init__(self, cfg):
        self._cfg = cfg

    @abstractmethod
    def is_configured(self):
        """True when this backend has what it needs to attempt a call at all
        (an API key, or — Anthropic only — a `claude` CLI found on PATH)."""

    @abstractmethod
    def converse(self, system_prompt, messages):
        """Returns the model's final text reply for this turn. Runs its own
        tool-use loop internally (web search, Gmail/Discord/browser connector
        tools) — callers never see intermediate tool_use turns. Raises on
        failure; the caller falls to the next configured provider, then to a
        canned apology."""


def get_llm_providers(cfg):
    """Returns the usable LLMProvider instances in the order they should be
    tried: the one `model.provider` ("anthropic" | "gemini" | "lmstudio") names
    first, then any other configured backend as failover. With no explicit
    choice the order is Anthropic first, for backward compatibility."""
    from .anthropic_provider import AnthropicProvider
    from .gemini_provider import GeminiProvider
    from .lmstudio_provider import LMStudioProvider

    ordered = [
        p for p in (AnthropicProvider(cfg), GeminiProvider(cfg), LMStudioProvider(cfg))
        if p.is_configured()
    ]

    choice = (cfg.get("model.provider") or "").strip().lower()
    ordered.sort(key=lambda p: p.name != choice)  # stable: chosen one first
    return ordered


def call_model(cfg, system_prompt, messages, fallback_text):
    """Tries each configured provider in order, returning the first successful
    reply. Deliberately broad except: two very differently-shaped backends
    (HTTP APIs, a subprocess CLI) fail in idiosyncratic ways, and there is
    always a safe last resort (fallback_text) below this loop."""
    for provider in get_llm_providers(cfg):
        try:
            return provider.converse(system_prompt, messages)
        except Exception as e:
            print(f"[jarvis] {provider.name} model call failed ({e}); trying next…", file=sys.stderr)
    return fallback_text
