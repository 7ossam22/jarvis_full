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
import time
import urllib.error
import urllib.request

from .llm import LLMProvider
from ..connectors.registry import GEMINI, registry

DEFAULT_MODEL = "gemini-flash-latest"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_gemini_contents(messages):
    """Our internal messages are plain {"role": "user"|"assistant", "content":
    str} turns (the tool-call back-and-forth stays local to each provider's own
    loop and never enters this list) — Gemini just wants "model" instead of
    "assistant"."""
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": str(m.get("content", ""))}]})
    return contents


def call_gemini(cfg, system_prompt, messages):
    api_key = cfg.get("model.gemini_api_key")
    model = cfg.get("model.gemini_model_id") or DEFAULT_MODEL

    # google_search is Gemini's own server-side tool; the declarations come from
    # the registry already converted to Gemini's shape and filtered to what this
    # provider is permitted to see.
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
        for attempt, backoff in enumerate((2, 5, 10, 20), start=1):
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"content-type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                # Only transient statuses are worth retrying; a 400/403 will
                # fail identically every time, so surface it immediately.
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                print(f"[jarvis] Gemini HTTP {e.code} (attempt {attempt}); retrying in {backoff}s…", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"[jarvis] Gemini request failed ({e}) (attempt {attempt}); retrying in {backoff}s…", file=sys.stderr)
            time.sleep(backoff)
        if body is None:
            raise TimeoutError("Gemini API unreachable after 4 retry attempts — giving up on this turn.")

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

    def converse(self, system_prompt, messages):
        return call_gemini(self._cfg, system_prompt, messages)
