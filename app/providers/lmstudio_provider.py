"""app/providers/lmstudio_provider.py — local LLM over LAN via LM Studio
(Model layer): the LMStudioProvider implementation of the LLMProvider
interface in llm.py.

LM Studio's "Local Server" exposes an OpenAI-compatible REST API (default
http://<host>:1234/v1). This provider talks to that endpoint with the same
OpenAI chat-completions + function-calling shape any OpenAI-compatible server
uses, and runs the same Gmail/Discord/browser/system/Jira connector tool loop
as the other providers — so a model running on another machine on the LAN
behaves identically to the rest of the app. There is no web-search tool for a
local model (LM Studio has none); everything else works.

Standard library only (urllib), same as every other provider here.

config.json:
    "model": {
      "provider": "lmstudio",
      "lmstudio_base_url": "http://192.168.1.50:1234/v1",
      "lmstudio_model_id": "",          # optional; LM Studio uses whatever model is loaded
      "lmstudio_api_key": "lm-studio",  # optional; LM Studio ignores it but the header must exist
      "lmstudio_use_tools": true         # optional; set false for models that choke on `tools`
    }
"""
import json
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

from .llm import LLMProvider
from ..connectors.registry import LMSTUDIO, registry

DEFAULT_MODEL = "local-model"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _to_openai_messages(system_prompt, messages):
    """Our internal turns are {"role": "user"|"assistant", "content": str};
    OpenAI accepts those verbatim, with the system prompt prepended."""
    out = [{"role": "system", "content": system_prompt}]
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        out.append({"role": role, "content": str(m.get("content", ""))})
    return out


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}


def _base_url(cfg):
    url = (cfg.get("model.lmstudio_base_url") or "").strip().rstrip("/")
    # Tolerate the user giving the host with or without the /v1 suffix.
    if url and not url.endswith("/v1"):
        url += "/v1"
    return url


def call_lmstudio(cfg, system_prompt, messages):
    base = _base_url(cfg)
    model = cfg.get("model.lmstudio_model_id") or DEFAULT_MODEL
    api_key = cfg.get("model.lmstudio_api_key") or "lm-studio"
    use_tools = cfg.get("model.lmstudio_use_tools")
    use_tools = True if use_tools is None else bool(use_tools)

    convo = _to_openai_messages(system_prompt, messages)
    payload = {"model": model, "messages": convo, "temperature": 0.7, "stream": False}
    if use_tools:
        # Already in OpenAI's {"type": "function", ...} shape and filtered to
        # what this provider may see — a local model is trusted with all of it.
        payload["tools"] = registry.get_tools_for_provider(LMSTUDIO)
        payload["tool_choice"] = "auto"

    url = f"{base}/chat/completions"
    for turn in range(80):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        )
        # Local models on modest hardware can be slow to first token.
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        choices = body.get("choices") or []
        if not choices:
            raise ValueError(f"LM Studio returned no choices: {str(body)[:200]}")
        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            # Echo the assistant turn (with its tool_calls) before the results.
            convo.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                result = registry.dispatch(name, _parse_args(fn.get("arguments")), LMSTUDIO, cfg)
                convo.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result),
                })
            payload["messages"] = convo
            continue

        return _THINK_RE.sub("", msg.get("content") or "").strip()

    return ""


# Reachability probes are cached so a burst of autopilot escalations does not
# re-knock on a dead host once per question. Short enough that starting LM
# Studio mid-session is picked up within seconds.
_reachable_cache = {}
_REACHABLE_TTL_S = 15.0
_PROBE_TIMEOUT_S = 1.5


class LMStudioProvider(LLMProvider):
    name = "lmstudio"

    def is_configured(self):
        return bool((self._cfg.get("model.lmstudio_base_url") or "").strip())

    def is_reachable(self):
        """One TCP knock at the LM Studio host, cached briefly.

        The machine serving this is on the LAN and gets switched off, and a
        chat request to a host that is not there blocks for the full 180s
        timeout in call_lmstudio() before failover even begins. That is a long
        time to look frozen, and it is intolerable on the form-autopilot path,
        whose whole purpose is to get UNstuck quickly. A connect probe costs
        about a millisecond when the host is up; the cache keeps a burst of
        escalations from re-probing a dead host once per question."""
        base = _base_url(self._cfg)
        now = time.monotonic()
        cached = _reachable_cache.get(base)
        if cached and now - cached[0] < _REACHABLE_TTL_S:
            return cached[1]

        parsed = urllib.parse.urlparse(base)
        host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        ok = False
        if host:
            try:
                socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_S).close()
                ok = True
            except OSError as e:
                print(f"[jarvis] LM Studio at {host}:{port} is not answering ({e}); "
                      f"it will not be tried first.", file=sys.stderr)
        _reachable_cache[base] = (now, ok)
        return ok

    def converse(self, system_prompt, messages):
        try:
            return call_lmstudio(self._cfg, system_prompt, messages)
        except Exception as e:
            print(f"[jarvis] LM Studio call failed ({e})", file=sys.stderr)
            raise
