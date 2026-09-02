"""app/providers/gemini_provider.py — Google Gemini text generation (Model
layer): the GeminiProvider implementation of the LLMProvider interface in
llm.py. Direct Gemini API (generateContent) with its own function-calling
tool-use loop, reusing the same Gmail/Discord/browser connector tool
definitions as the Anthropic provider — only the request/response shapes
differ, so the two providers behave identically to the rest of the app.

Standard library only (urllib), same as every other provider here. No CLI
fallback: unlike Anthropic (which can fall back to a locally logged-in
`claude` subscription), there is no equivalent free local fallback for
Gemini in this app, so this provider is simply skipped when unconfigured.
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request

from .llm import LLMProvider
from .. import telemetry, vision
from ..connectors.registry import GEMINI, registry

DEFAULT_MODEL = "gemini-flash-latest"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


# ---- free-tier quota handling ----------------------------------------------
# A 429 here is never an authentication problem: a key that gets one still
# authenticates fine. It is RESOURCE_EXHAUSTED — a quota — and the two quotas
# behind it fail very differently, so treating them alike is what turns a
# recoverable pause into a dead run. A per-MINUTE quota clears on its own in
# well under a minute; a per-DAY one will not clear for hours, and retrying it
# only burns the turn's time budget before failing anyway.
_PACE_LOCK = threading.Lock()
_pace = {"min_interval": 0.0, "next_at": 0.0}

# Free tier allows roughly 10 requests/minute on the flash models, but one
# autopilot turn chains dozens of tool rounds back to back with no gap at all —
# so a long run rate-limits ITSELF within seconds. The first per-minute 429 is
# therefore the signal to start spacing requests for the rest of the process,
# rather than to keep sprinting into the same wall. Costs nothing when quota is
# ample: until a 429 actually happens the interval stays zero.
_LEARNED_INTERVAL_S = 6.5
_MAX_SERVER_DELAY_S = 60.0


def _quota_info(raw):
    """Reads Google's structured 429 body: how long it says to wait
    (RetryInfo), and whether the exhausted quota is a daily one (QuotaFailure)
    — i.e. one there is no point waiting for."""
    try:
        err = (json.loads(raw) or {}).get("error") or {}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None, False, str(raw)[:200]

    delay, daily = None, False
    for d in err.get("details") or []:
        kind = str(d.get("@type", ""))
        if kind.endswith("RetryInfo"):
            try:                                    # "17s", "1.5s"
                delay = float(str(d.get("retryDelay", "")).rstrip("s"))
            except ValueError:
                pass
        elif kind.endswith("QuotaFailure"):
            for v in d.get("violations") or []:
                blob = f"{v.get('quotaId', '')} {v.get('quotaMetric', '')}"
                if "PerDay" in blob or "per_day" in blob:
                    daily = True
    return delay, daily, err.get("message") or ""


def _respect_pace():
    """Sleeps just long enough to keep the learned minimum gap between
    requests. Reserves its slot inside the lock so concurrent chat jobs queue
    behind each other instead of all firing at once."""
    with _PACE_LOCK:
        now = time.monotonic()
        start = max(now, _pace["next_at"])
        _pace["next_at"] = start + _pace["min_interval"]
    if start > now:
        time.sleep(start - now)


def _to_gemini_contents(messages):
    """Our internal messages are plain {"role": "user"|"assistant", "content":
    str} turns (the tool-call back-and-forth stays local to each provider's own
    loop and never enters this list) — Gemini just wants "model" instead of
    "assistant"."""
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role,
                         "parts": vision.to_gemini_parts(str(m.get("content", "")),
                                                         vision.images_of(m))})
    return contents


def call_gemini(cfg, system_prompt, messages):
    api_key = cfg.get("model.gemini_api_key")
    model = cfg.get("model.gemini_model_id") or DEFAULT_MODEL

    # google_search is Gemini's own server-side tool: a real search index, run
    # on Google's side, and markedly better than the registry's web_search
    # (which scrapes DuckDuckGo Lite for backends that have no search at all).
    # There is no name clash between the two — Anthropic's built-in happens to
    # be called `web_search` and does clash, Gemini's does not — so nothing
    # forced its removal, and removing it was a straight quality loss. The
    # registry supplies everything else, already converted to Gemini's shape,
    # web_fetch included, so the model can still read a page it found.
    tools = [
        {"google_search": {}},
        {"function_declarations": registry.get_tools_for_provider(GEMINI)},
    ]

    contents = _to_gemini_contents(messages)
    payload_dict = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": tools,
        # Required as of the current API whenever a built-in tool (google_search)
        # is combined with custom function_declarations in one request — without
        # it Gemini rejects the call with a 400 INVALID_ARGUMENT.
        "tool_config": {"include_server_side_tool_invocations": True},
    }

    url = f"{API_BASE}/{model}:generateContent?key={api_key}"

    # Long automation workflows (fill form / complete visit) chain hundreds of
    # tool rounds; a stall here used to freeze the whole workflow until the
    # user prodded it again. Two past causes, both fixed at this layer:
    # a single slow/rate-limited Gemini response killed the run (now retried
    # with backoff below), and the old 80-round cap silently returned ""
    # mid-visit (now a generous cap that reports instead of going quiet).
    for turn in range(500):
        payload = json.dumps(payload_dict).encode("utf-8")
        body = None
        for attempt, backoff in enumerate((2, 5, 10, 20, 30, 60), start=1):
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"content-type": "application/json"},
            )
            try:
                _respect_pace()
                with urllib.request.urlopen(req, timeout=90) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                # Only transient statuses are worth retrying; a 400/403 will
                # fail identically every time, so surface it immediately.
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                wait = backoff
                if e.code == 429:
                    delay, daily, detail = _quota_info(e.read().decode("utf-8", "replace"))
                    if daily:
                        telemetry.activity(
                            "Gemini's free daily quota is used up — switching backend",
                            notable=True)
                        # Retrying a daily quota just spends the run's minutes
                        # to fail the same way, and reporting it as "the API is
                        # unreachable" sends the reader hunting for a network
                        # or key problem that does not exist.
                        raise RuntimeError(
                            "Gemini's free-tier DAILY quota is exhausted — no amount of "
                            "retrying clears it. Wait for the daily reset, switch "
                            "model.provider, or enable billing on the key. "
                            f"({detail[:200]})") from e
                    with _PACE_LOCK:
                        _pace["min_interval"] = max(_pace["min_interval"], _LEARNED_INTERVAL_S)
                    if delay is not None:
                        # Google says exactly how long it wants; guessing a
                        # shorter ladder just spends attempts on certain 429s.
                        wait = min(max(delay, backoff), _MAX_SERVER_DELAY_S)
                    print(f"[jarvis] Gemini rate-limited (attempt {attempt}); pacing requests "
                          f"{_LEARNED_INTERVAL_S}s apart and waiting {wait:.0f}s…", file=sys.stderr)
                    # A rate-limit wait is the single longest silence in this
                    # app, and from outside it is indistinguishable from a
                    # hang. Say so, and say it out loud the first time.
                    telemetry.activity(
                        f"Gemini is rate-limiting me — waiting {wait:.0f}s, attempt {attempt} of 6",
                        notable=(attempt == 1))
                else:
                    print(f"[jarvis] Gemini HTTP {e.code} (attempt {attempt}); retrying in {wait}s…", file=sys.stderr)
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"[jarvis] Gemini request failed ({e}) (attempt {attempt}); retrying in {backoff}s…", file=sys.stderr)
                time.sleep(backoff)
        if body is None:
            raise TimeoutError(
                "Gemini did not answer after 6 attempts spanning ~2 minutes — most likely a "
                "sustained free-tier rate limit. Giving up on this turn.")

        if turn:
            telemetry.activity(f"Thinking… ({turn} steps so far)")

        candidates = body.get("candidates") or []
        if not candidates:
            block_reason = (body.get("promptFeedback") or {}).get("blockReason")
            raise ValueError(f"Gemini returned no candidates (block reason: {block_reason})")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if function_calls:
            # The model's turn (including its thoughtSignature) must be echoed
            # back verbatim before the tool results, or the next call 400s.
            contents.append({"role": "model", "parts": parts})
            response_parts = []
            for call in function_calls:
                tool_name = call.get("name", "")
                tool_input = call.get("args") or {}
                result_data = registry.dispatch(tool_name, tool_input, GEMINI, cfg)
                response_parts.append({
                    "functionResponse": {
                        "name": tool_name,
                        "response": {"result": result_data},
                        # Echoes the functionCall's own "id" — required to match
                        # the call it answers, the same role tool_use_id plays
                        # on the Anthropic side.
                        "id": call.get("id"),
                    }
                })
            # Tool results go back as role "user", not "function" — the
            # Gemini API rejects a "function" role turn with a 400.
            contents.append({"role": "user", "parts": response_parts})
            payload_dict["contents"] = contents
            continue

        return "".join(p.get("text", "") for p in parts)

    # Round budget exhausted mid-workflow — say so instead of returning ""
    # (an empty reply looks to the user like the assistant simply froze).
    return ("I had to pause, sir — this task used up my action budget of 500 tool "
            "rounds in one turn. Say \"continue\" and I will pick up exactly where I left off.")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def is_configured(self):
        key = self._cfg.get("model.gemini_api_key") or ""
        return bool(key.strip()) and "PUT-YOUR" not in key

    def supports_vision(self):
        return True

    def converse(self, system_prompt, messages):
        return call_gemini(self._cfg, system_prompt, messages)
