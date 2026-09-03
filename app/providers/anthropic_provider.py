"""app/providers/anthropic_provider.py — Anthropic Claude text generation
(Model layer): the AnthropicProvider implementation of the LLMProvider
interface in llm.py. Direct API with a custom tool-use loop & remote MCP
server support, falling back to the `claude -p` CLI (useful with no API key
but a logged-in Claude Code subscription) when the API is unreachable.
"""
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from .llm import LLMProvider
from .. import vision
from ..connectors.registry import ANTHROPIC, registry


def call_anthropic(cfg, system_prompt, messages):
    api_key = cfg.get("model.provider_api_key")
    model = cfg.get("model.model_id") or "claude-sonnet-5"

    # web_search is Anthropic's own server-side tool — a real search index, far
    # better than the registry's DuckDuckGo scraper, which exists for backends
    # that have no search of their own. The registry deliberately withholds its
    # `web_search` from this provider (only_llm=True there): both are named
    # `web_search`, and two tools sharing a name in one request is an API
    # validation error. Everything else comes from the registry in Anthropic's
    # wire shape, web_fetch included.
    tools: list[dict] = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
    ]
    tools.extend(registry.get_tools_for_provider(ANTHROPIC))

    # Images ride as content blocks; a turn without them stays a plain string,
    # so ordinary conversations send byte-identical payloads to before.
    curr_messages = [
        {"role": m.get("role", "user"),
         "content": vision.to_anthropic_content(str(m.get("content", "")),
                                                vision.images_of(m))}
        if isinstance(m, dict) and vision.images_of(m) else m
        for m in messages
    ]
    payload_dict = {
        "model": model,
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": curr_messages,
        "tools": tools,
    }

    mcp_servers = cfg.get("mcp_servers")
    if mcp_servers:
        payload_dict["mcp_servers"] = mcp_servers

    for turn in range(80):
        payload = json.dumps(payload_dict).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        stop_reason = body.get("stop_reason")
        content_blocks = body.get("content", [])

        if stop_reason == "tool_use":
            curr_messages.append({"role": "assistant", "content": content_blocks})

            tool_results = []
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    tool_id = block.get("id")
                    tool_name = block.get("name")
                    tool_input = block.get("input", {})

                    result_data = registry.dispatch(tool_name, tool_input, ANTHROPIC, cfg)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result_data)
                    })

            curr_messages.append({"role": "user", "content": tool_results})
            payload_dict["messages"] = curr_messages
            continue

        return "".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")

    return ""


from .cli_provider import (
    CONNECTOR_HINTS,
    is_cli_available,
    call_cli_fallback,
    call_cli_turn,
    CLI_CLAUDE,
)


def call_claude_cli(cfg, system_prompt, messages):
    return call_cli_turn(cfg, system_prompt, messages, cli_name=CLI_CLAUDE, provider_name=ANTHROPIC)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def is_configured(self):
        return self._cfg.has_real_api_key() or is_cli_available(self._cfg, preferred=CLI_CLAUDE)

    def supports_vision(self):
        # Only the direct API carries image blocks. The CLI fallback
        # takes a single text prompt, so a frame cannot reach it at all.
        return self._cfg.has_real_api_key()

    def converse(self, system_prompt, messages):
        cfg = self._cfg
        if cfg.has_real_api_key():
            try:
                return call_anthropic(cfg, system_prompt, messages)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
                print(f"[jarvis] Anthropic API call failed ({e}); trying CLI fallback…", file=sys.stderr)
        return call_cli_fallback(cfg, system_prompt, messages, preferred=CLI_CLAUDE, provider_name=ANTHROPIC)



