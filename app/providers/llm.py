"""app/providers/llm.py — LLM provider interface & selection (Model layer).

LLMProvider is the interface every model backend implements; get_llm_providers()
/call_model() are the dependency-injection point — the exact same pattern as
providers/tts.py's TTSProvider: callers only ever talk to the interface,
`model.provider` in config picks the backend explicitly, and any other
configured backend serves as automatic failover.

Available providers (app/providers/*_provider.py):
  - "anthropic" (anthropic_provider.py) — Claude via the direct API
    (model.provider_api_key + model.model_id), falling back to CLI (claude/agy)
    when no API key is set or the API call fails.
  - "gemini" (gemini_provider.py) — Google Gemini via the direct API
    (model.gemini_api_key + model.gemini_model_id), falling back to CLI (agy/claude)
    when unconfigured or quota-limited.
  - "lmstudio" (lmstudio_provider.py) — a local model on the LAN served by
    LM Studio's OpenAI-compatible server (model.lmstudio_base_url, e.g.
    http://192.168.1.50:1234/v1). No web-search tool; connector tools work.
  - "agy" / "claude" (cli_provider.py) — direct execution via local CLI without
    an API key.

CLI Fallback configuration, in config.json:
    "model": { "cli_fallback": "agy" }     # or "claude" / "auto" / "none"
Leave "cli_fallback" empty or "auto" to automatically pick the best installed CLI
with automatic failover between them if one fails.
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

    def supports_vision(self):
        """True when this backend can actually LOOK at an image in a turn.

        Separate from being configured or reachable, because a backend can be
        both and still be blind — the `claude` CLI fallback flattens a turn to
        text, so an attached camera frame vanishes on the way out. Silently
        handing a picture to a blind backend is the worst outcome available:
        the model sees the note saying a frame was captured, sees no frame, and
        narrates the confusion back to the user as though the camera failed.
        """
        return False

    def is_reachable(self):
        """True when this backend can plausibly be reached right now.

        Separate from is_configured(), which only asks whether the settings
        exist. For the hosted APIs there is nothing cheap and honest to test —
        their own retry loops handle a flaky internet — so the default is True.
        A local backend on the LAN is different: the machine serving it is
        routinely just switched off, and that is worth one cheap probe, because
        preferring a host that is not there costs a full request timeout before
        anything else is even tried. Used for ORDERING only, never to drop a
        provider: a probe that is wrong must not be able to leave the app with
        no brain at all."""
        return True

    @abstractmethod
    def converse(self, system_prompt, messages):
        """Returns the model's final text reply for this turn. Runs its own
        tool-use loop internally (web search, Gmail/Discord/browser connector
        tools) — callers never see intermediate tool_use turns. Raises on
        failure; the caller falls to the next configured provider, then to a
        canned apology."""


def get_llm_providers(cfg, prefer=None):
    """Returns the usable LLMProvider instances in the order they should be
    tried: the one `model.provider` ("anthropic" | "gemini" | "lmstudio") names
    first, then any other configured backend as failover. With no explicit
    choice the order is Anthropic first, for backward compatibility.

    `prefer` overrides `model.provider` for one call site without changing the
    app-wide choice. It exists because not every caller wants the same backend:
    the chat you talk to and the one-shot question the form autopilot asks when
    it gets stuck have opposite needs. A conversation wants the strongest model
    available; the autopilot fires dozens of tiny constrained-JSON questions in
    a burst, which is exactly the shape that exhausts a metered per-minute
    quota — and it needs none of that strength to answer them. An unconfigured
    or misspelled preference is not an error: the list is merely left in its
    default order, so the request still gets answered.
    """
    from .anthropic_provider import AnthropicProvider
    from .gemini_provider import GeminiProvider
    from .lmstudio_provider import LMStudioProvider
    from .cli_provider import CLIProvider, CLI_AGY, CLI_CLAUDE

    candidate_providers = [
        AnthropicProvider(cfg),
        GeminiProvider(cfg),
        LMStudioProvider(cfg),
        CLIProvider(cfg, cli_type=CLI_AGY),
        CLIProvider(cfg, cli_type=CLI_CLAUDE),
    ]

    ordered = [p for p in candidate_providers if p.is_configured()]

    choice = (prefer or (cfg.get("model.provider") if cfg else "") or "").strip().lower()
    if choice in ("antigravity", "gemini-cli"):
        choice = CLI_AGY
    # Stable sort: the chosen backend goes first, but only if it answers a
    # knock. An unreachable choice keeps its natural place in the list rather
    # than being dropped, so it is still tried — just not ahead of backends
    # that are actually up.
    ordered.sort(key=lambda p: not (p.name == choice and p.is_reachable()))
    return ordered



def call_model(cfg, system_prompt, messages, fallback_text, prefer=None,
               needs_vision=False):
    """Tries each configured provider in order, returning the first successful
    reply. Deliberately broad except: two very differently-shaped backends
    (HTTP APIs, a subprocess CLI) fail in idiosyncratic ways, and there is
    always a safe last resort (fallback_text) below this loop.

    `prefer` names a backend to try first for this call only — see
    get_llm_providers(). Failover is unchanged: preferring a backend that is
    down still reaches the others."""
    from .. import telemetry

    providers = get_llm_providers(cfg, prefer=prefer)

    if needs_vision:
        # A turn carrying a camera frame may only go to a backend that can see
        # it. Ordering is not enough — falling through to a blind one would
        # answer a question about a picture without the picture.
        seeing = [p for p in providers if p.supports_vision()]
        if not seeing:
            names = ", ".join(p.name for p in providers) or "none"
            telemetry.record("error", "a camera frame was captured but no backend can see",
                             f"configured: {names}")
            return ("I captured the frame, sir, but none of my configured models can "
                    "actually look at an image right now. Set model.provider_api_key for "
                    "Claude, or use the Gemini key — the `claude` CLI fallback is text-only.")
        providers = seeing

    failures = []
    for provider in providers:
        try:
            telemetry.activity(f"Asking {provider.name}…")
            return provider.converse(system_prompt, messages)
        except Exception as e:
            failures.append(f"{provider.name}: {e}")
            print(f"[jarvis] {provider.name} model call failed ({e}); trying next…", file=sys.stderr)
            telemetry.record("provider", f"{provider.name} failed, trying the next backend", e)
            # Falling back between backends is slow and completely invisible;
            # unannounced it reads as the app having frozen.
            telemetry.activity(f"{provider.name} is not answering — trying another backend",
                               notable=True)

    # Every backend failed. Returning the canned apology alone would present a
    # total outage as an ordinary answer, so say what actually happened — a
    # silent fallback is how a broken run looks like a working one.
    if failures:
        telemetry.record("error", "every model backend failed", "; ".join(failures))
        return (fallback_text + " (Nothing answered: " + "; ".join(failures)[:300] + ")")
    telemetry.record("error", "no model backend is configured")
    return fallback_text
