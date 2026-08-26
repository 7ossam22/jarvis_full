"""app/providers/anthropic_provider.py — model calling (Model layer):
direct Anthropic API with custom tool-use loop & remote MCP server support,
falling back to `claude -p` CLI or canned response.
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

from ..connectors.gmail import get_gmail_tools, execute_gmail_tool
from ..connectors.discord import get_discord_tools, execute_discord_tool


def call_anthropic(cfg, system_prompt, messages):
    api_key = cfg.get("model.provider_api_key")
    model = cfg.get("model.model_id") or "claude-sonnet-5"

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
    ]
    tools.extend(get_gmail_tools())
    tools.extend(get_discord_tools())

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

    for turn in range(5):
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


def _parse_discord_send_intent(last_msg):
    import re
    raw = last_msg.strip()
    channel = "general"
    content = ""

    q_match = re.search(r'["\']([^"\'\n]+)["\']', raw)
    if q_match:
        content = q_match.group(1).strip()

    if not content:
        s_match = re.search(r'(?:saying|that says|with content|content:?|message:?|with text)\s+(.+)$', raw, re.IGNORECASE)
        if s_match:
            content = s_match.group(1).strip()

    ch_match = re.search(r'(?:to|in|channel)\s+#?([A-Za-z0-9_\-]+)', raw, re.IGNORECASE)
    if ch_match:
        ch = ch_match.group(1).lower()
        if ch not in ["discord", "the", "a", "my", "server", "bot", "channel", "messages", "message"]:
            channel = ch

    if not content:
        cleaned = raw
        cleaned = re.sub(r'^(?:please\s+)?(?:send|post|write|say|tell|publish)\b(?:\s+a|\s+the|\s+this)?(?:\s+message|\s+chat|\s+text|\s+notice)?', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'^(?:to|in|on)\s+(?:the\s+)?(?:discord\s+)?(?:server\s+)?(?:channel\s+)?(?:general|jarvis-notice|#?[A-Za-z0-9_\-]+)?(?:\s+in\s+discord|\s+on\s+discord|\s+to\s+discord)?\s*', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'^(?:in\s+discord|on\s+discord|to\s+discord|discord)\s*', '', cleaned, flags=re.IGNORECASE).strip()

        if cleaned and len(cleaned) > 2:
            content = cleaned

    if not content:
        content = "Greetings from JARVIS! All systems operational, sir."

    return channel, content


def call_claude_cli(cfg, system_prompt, messages):
    last_msg = str(messages[-1]["content"]) if messages else ""
    last_msg_lower = last_msg.lower()
    extra_context = ""

    if any(k in last_msg_lower for k in ["email", "inbox", "gmail", "mail"]):
        res = execute_gmail_tool(cfg, "gmail_get_latest_emails", {"max_results": 5})
        extra_context = f"\n\n[GMAIL CONNECTOR TOOL RESULT]:\n{json.dumps(res)}\n"
    elif "discord" in last_msg_lower and any(k in last_msg_lower for k in ["send", "post", "write", "say", "message"]):
        ch_target, msg_text = _parse_discord_send_intent(last_msg)
        res = execute_discord_tool(cfg, "discord_send_message", {"channel_id": ch_target, "content": msg_text})
        extra_context = f"\n\n[DISCORD CONNECTOR TOOL RESULT]:\n{json.dumps(res)}\n"
    elif any(k in last_msg_lower for k in ["discord", "guild", "discord server", "discord chat"]):
        res = execute_discord_tool(cfg, "discord_get_user_guilds", {})
        extra_context = f"\n\n[DISCORD CONNECTOR TOOL RESULT]:\n{json.dumps(res)}\n"

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



def call_model(cfg, system_prompt, messages, fallback_text):
    if cfg.has_real_api_key():
        try:
            return call_anthropic(cfg, system_prompt, messages)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            print(f"[jarvis] Anthropic API call failed ({e}); trying claude CLI…", file=sys.stderr)
    try:
        return call_claude_cli(cfg, system_prompt, messages)
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, OSError) as e:
        print(f"[jarvis] claude CLI fallback failed ({e})", file=sys.stderr)
    return fallback_text


