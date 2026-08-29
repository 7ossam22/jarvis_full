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
import sys
import urllib.request

from .llm import LLMProvider
from ..connectors.gmail import get_gmail_tools, execute_gmail_tool
from ..connectors.discord import get_discord_tools, execute_discord_tool
from ..connectors.browser import get_browser_tools, execute_browser_tool
from ..connectors.system import get_system_tools, execute_system_tool
from ..connectors.jira import get_jira_tools, execute_jira_tool

DEFAULT_MODEL = "local-model"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _execute_tool(cfg, tool_name, tool_input):
    if tool_name.startswith("gmail_"):
        return execute_gmail_tool(cfg, tool_name, tool_input)
    elif tool_name.startswith("discord_"):
        return execute_discord_tool(cfg, tool_name, tool_input)
    elif tool_name.startswith("browser_") or tool_name == "system_open":
        return execute_browser_tool(cfg, tool_name, tool_input)
    elif tool_name.startswith("system_"):
        return execute_system_tool(cfg, tool_name, tool_input)
    elif tool_name.startswith("jira_"):
        return execute_jira_tool(cfg, tool_name, tool_input)
    return {"error": f"Tool {tool_name} not found"}


def _to_openai_tools(tool_specs):
    """Anthropic-style specs -> OpenAI `tools` array. Both use an OpenAPI-subset
    JSON Schema for parameters, so this is a rename."""
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
        }}
        for t in tool_specs
    ]


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
        payload["tools"] = _to_openai_tools(
            get_gmail_tools() + get_discord_tools() + get_browser_tools()
            + get_system_tools() + get_jira_tools()
        )
        payload["tool_choice"] = "auto"

    url = f"{base}/chat/completions"
    for turn in range(20):
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
                result = _execute_tool(cfg, name, _parse_args(fn.get("arguments")))
                convo.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result),
                })
            payload["messages"] = convo
            continue

        return _THINK_RE.sub("", msg.get("content") or "").strip()

    return ""


class LMStudioProvider(LLMProvider):
    name = "lmstudio"

    def is_configured(self):
        return bool((self._cfg.get("model.lmstudio_base_url") or "").strip())

    def converse(self, system_prompt, messages):
        try:
            return call_lmstudio(self._cfg, system_prompt, messages)
        except Exception as e:
            print(f"[jarvis] LM Studio call failed ({e})", file=sys.stderr)
            raise
