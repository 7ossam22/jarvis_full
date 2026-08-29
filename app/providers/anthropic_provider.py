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
from ..connectors.gmail import get_gmail_tools, execute_gmail_tool
from ..connectors.discord import get_discord_tools, execute_discord_tool
from ..connectors.browser import get_browser_tools, execute_browser_tool
from ..connectors.system import get_system_tools, execute_system_tool
from ..connectors.jira import get_jira_tools, execute_jira_tool


def call_anthropic(cfg, system_prompt, messages):
    api_key = cfg.get("model.provider_api_key")
    model = cfg.get("model.model_id") or "claude-sonnet-5"

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
    ]
    tools.extend(get_gmail_tools())
    tools.extend(get_discord_tools())
    tools.extend(get_browser_tools())
    tools.extend(get_system_tools())
    tools.extend(get_jira_tools())

    curr_messages = list(messages)
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

    for turn in range(20):
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

                    if tool_name.startswith("gmail_"):
                        result_data = execute_gmail_tool(cfg, tool_name, tool_input)
                    elif tool_name.startswith("discord_"):
                        result_data = execute_discord_tool(cfg, tool_name, tool_input)
                    elif tool_name.startswith("browser_") or tool_name == "system_open":
                        result_data = execute_browser_tool(cfg, tool_name, tool_input)
                    elif tool_name.startswith("system_"):
                        result_data = execute_system_tool(cfg, tool_name, tool_input)
                    elif tool_name.startswith("jira_"):
                        result_data = execute_jira_tool(cfg, tool_name, tool_input)
                    else:
                        result_data = {"error": f"Tool {tool_name} not found"}

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


# Cheap pre-filter: only run the (slow) LLM tool-decision call when the recent
# conversation plausibly involves a connector at all. Checked against the last
# few turns, not just the last sentence — "now share it there too" must still
# trigger when Discord came up a turn earlier.
CONNECTOR_HINTS = (
    "discord", "email", "gmail", "mail", "inbox", "send", "post",
    "share", "message", "dm", "channel",
    "open", "close", "browser", "tab", "tabs", "website", "launch", "chrome",
    "profile", "profiles", "switch", "click", "type", "scroll", "screenshot",
    "jira", "ticket", "issue", "volume", "sound", "stats", "app",
    "flutter", "patrol", "canvaskit", "widget", "widgets", "semantics",
)


def _decide_connector_action(cfg, messages):
    """One small `claude -p` call that decides whether the latest user turn
    needs a connector tool (Gmail/Discord/Browser/System/Jira) and with what arguments, given the
    whole recent conversation — replaces brittle keyword parsing of just the
    last sentence. Returns {"tool": name, "input": {...}} or None."""
    transcript = "\n\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages[-6:]
    )
    tool_specs = (
        get_gmail_tools()
        + get_discord_tools()
        + get_browser_tools()
        + get_system_tools()
        + get_jira_tools()
    )
    tools_desc = json.dumps(
        [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
         for t in tool_specs]
    )
    sys_p = (
        "You are the tool-routing brain for a voice assistant. Given a conversation, decide "
        "whether the LAST user turn requires calling one of these tools:\n" + tools_desc + "\n\n"
        "Rules:\n"
        "- Respond ONLY with raw JSON, nothing else.\n"
        '- No tool needed (questions, lookups, small talk): {"tool": "none"}\n'
        '- Tool needed: {"tool": "<name>", "input": {<arguments matching its input_schema>}}\n'
        "- Resolve every back-reference ('send this', 'that screenshot', 'what you found') "
        "against the earlier turns: tool inputs must contain the ACTUAL resolved content, "
        "self-contained and understandable with no other context — never the literal "
        "referring words.\n"
        "- When sharing content that has a '[reference links: ...]' footnote in the "
        "conversation, append the most relevant link to the message/body being sent.\n"
        "- For discord_send_message, channel_id may be a channel NAME like 'general' — it is "
        "resolved automatically. Default to 'general' when the user names no channel.\n"
        "- browser_open_url, browser_list_tabs, browser_switch_tab, browser_close, browser_detect_app_type, "
        "browser_flutter_get_widgets, browser_flutter_click, browser_flutter_type, flutter_run_test are for browser & Flutter apps: "
        "use them when the user asks to open a website/Flutter app, check open tabs, interact with widgets, or run Flutter tests.\n"
    )
    try:
        res = subprocess.run(
            ["claude", "-p", transcript, "--system-prompt", sys_p],
            capture_output=True, text=True, timeout=45,
            stdin=subprocess.DEVNULL,
        )
        out = res.stdout.strip()
        if "{" in out and "}" in out:
            parsed = json.loads(out[out.find("{"):out.rfind("}")+1])
            if parsed.get("tool") and parsed["tool"] != "none":
                return parsed
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as e:
        print(f"[jarvis] connector decision failed ({e})", file=sys.stderr)
    return None


def call_claude_cli(cfg, system_prompt, messages):
    extra_context = ""
    recent_text = " ".join(str(m.get("content", "")) for m in messages[-5:]).lower()
    if any(k in recent_text for k in CONNECTOR_HINTS):
        action = _decide_connector_action(cfg, messages)
        if action:
            tool_name = action.get("tool", "")
            tool_input = action.get("input") or {}
            if tool_name.startswith("gmail_"):
                result = execute_gmail_tool(cfg, tool_name, tool_input)
            elif tool_name.startswith("discord_"):
                result = execute_discord_tool(cfg, tool_name, tool_input)
            elif tool_name.startswith("browser_") or tool_name.startswith("flutter_") or tool_name == "system_open":
                result = execute_browser_tool(cfg, tool_name, tool_input)
            elif tool_name.startswith("system_"):
                result = execute_system_tool(cfg, tool_name, tool_input)
            elif tool_name.startswith("jira_"):
                result = execute_jira_tool(cfg, tool_name, tool_input)
            else:
                result = None
            if result is not None:
                print(f"[jarvis] connector call {tool_name}({json.dumps(tool_input)[:200]}) -> "
                      f"{json.dumps(result)[:300]}", file=sys.stderr)
                extra_context = (
                    f"\n\n[CONNECTOR TOOL RESULT — {tool_name} was already executed by the "
                    "system on the user's behalf. Report its outcome truthfully: on success, "
                    "confirm what was done; on error, relay the stated reason. Do not claim "
                    "anything is unconfigured unless this result says so.]:\n"
                    f"{json.dumps(result)}\n"
                )

    convo = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    full_prompt = f"{convo}{extra_context}\n\nASSISTANT:"
    result = subprocess.run(
        ["claude", "-p", full_prompt, "--system-prompt", system_prompt, "--allowedTools", "WebSearch,WebFetch"],
        capture_output=True, text=True, timeout=90,
        stdin=subprocess.DEVNULL,
    )


    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr or "claude CLI returned no output")
    return result.stdout.strip()



class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def is_configured(self):
        return self._cfg.has_real_api_key() or shutil.which("claude") is not None

    def converse(self, system_prompt, messages):
        cfg = self._cfg
        if cfg.has_real_api_key():
            try:
                return call_anthropic(cfg, system_prompt, messages)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
                print(f"[jarvis] Anthropic API call failed ({e}); trying claude CLI…", file=sys.stderr)
        return call_claude_cli(cfg, system_prompt, messages)


