"""app/connectors/discord.py — Discord integration & tool definitions (Connector layer).

Provides standard Discord tools (discord_get_recent_messages, discord_send_message,
discord_get_user_guilds, discord_get_guild_channels) interfacing directly with the
Discord REST API (v10). Standard library only — zero pip dependencies.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DISCORD_API_BASE = "https://discord.com/api/v10"

# Channel-name → ID cache. Resolving a name costs one API call per guild
# (listing every channel), and Discord rate-limits those routes hard —
# without a cache, back-to-back sends intermittently 429 and fail even
# though the token is fine.
_CHANNEL_CACHE = {}
_CHANNEL_CACHE_AT = 0.0
_CHANNEL_CACHE_TTL = 600  # seconds


class DiscordAPIError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(f"HTTP {code}: {detail}")


def get_discord_tools():
    """Returns Anthropic API tool definitions for Discord operations."""
    return [
        {
            "name": "discord_get_recent_messages",
            "description": "Fetch recent chat messages from a specific Discord channel.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "The Discord channel ID (numeric ID string)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of messages to retrieve (1-20, default 10).",
                        "default": 10
                    }
                },
                "required": ["channel_id"]
            }
        },
        {
            "name": "discord_send_message",
            "description": "Send a text message to a specific Discord channel.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "The Discord channel ID."
                    },
                    "content": {
                        "type": "string",
                        "description": "The message text content to send."
                    }
                },
                "required": ["channel_id", "content"]
            }
        },
        {
            "name": "discord_get_user_guilds",
            "description": "List Discord servers (guilds) the bot or user is a member of.",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "discord_get_guild_channels",
            "description": "List channels in a specific Discord server (guild).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "guild_id": {
                        "type": "string",
                        "description": "The Discord server/guild ID."
                    }
                },
                "required": ["guild_id"]
            }
        }
    ]


def _make_discord_request(token, endpoint, params=None, method="GET", body_data=None, _retried=False):
    url = f"{DISCORD_API_BASE}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    # Supports Bot Token ("Bot ...") or User Token / OAuth Bearer
    auth_header = token if token.startswith("Bot ") or token.startswith("Bearer ") else f"Bot {token}"

    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
        "User-Agent": "JarvisDiscordConnector/1.0"
    }

    data = json.dumps(body_data).encode("utf-8") if body_data else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            detail = e.reason
        # Rate limited: honor Retry-After once (bounded) instead of failing —
        # this is the usual cause of "the token sometimes doesn't work".
        if e.code == 429 and not _retried:
            try:
                wait = float(e.headers.get("Retry-After") or json.loads(detail).get("retry_after") or 1.0)
            except (ValueError, TypeError, json.JSONDecodeError):
                wait = 1.0
            time.sleep(min(wait, 5.0))
            return _make_discord_request(token, endpoint, params, method, body_data, _retried=True)
        raise DiscordAPIError(e.code, detail)


def _resolve_channel_id(token, channel_input):
    """Resolves channel name (e.g. 'general' or '#general') or channel ID to
    numeric ID. Caches every text channel seen during the scan so repeated
    sends don't re-list all guilds/channels each time (the rate-limit trap)."""
    global _CHANNEL_CACHE_AT
    if not channel_input:
        channel_input = "general"
    if str(channel_input).isdigit():
        return str(channel_input)

    target_name = str(channel_input).lstrip("#").strip().lower()

    if time.time() - _CHANNEL_CACHE_AT > _CHANNEL_CACHE_TTL:
        _CHANNEL_CACHE.clear()
    if target_name in _CHANNEL_CACHE:
        return _CHANNEL_CACHE[target_name]

    try:
        first_text_channel = None
        guilds = _make_discord_request(token, "users/@me/guilds")
        for g in guilds:
            channels = _make_discord_request(token, f"guilds/{g['id']}/channels")
            for c in channels:
                if c.get("type") == 0 and c.get("name"):
                    _CHANNEL_CACHE[c["name"].lower()] = c.get("id")
                    if first_text_channel is None:
                        first_text_channel = c.get("id")
        _CHANNEL_CACHE_AT = time.time()
        if target_name in _CHANNEL_CACHE:
            return _CHANNEL_CACHE[target_name]
        if first_text_channel:
            return first_text_channel
    except Exception as e:
        print(f"[jarvis] Discord channel resolution for '{target_name}' failed: {e}", file=sys.stderr)
    return channel_input


def execute_discord_tool(cfg, tool_name, tool_input):
    """Executes a Discord tool call using configured Bot Token or User Token."""
    token = cfg.get("discord.bot_token") or cfg.get("discord.token") or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        return {
            "error": "Discord token not configured. Please set 'discord.bot_token' in config.json."
        }

    try:
        if tool_name == "discord_get_recent_messages":
            raw_channel = tool_input.get("channel_id")
            channel_id = _resolve_channel_id(token, raw_channel)
            limit = min(tool_input.get("limit", 10), 20)
            res = _make_discord_request(token, f"channels/{channel_id}/messages", params={"limit": limit})
            messages = []
            for m in res:
                messages.append({
                    "id": m.get("id"),
                    "author": m.get("author", {}).get("username", "Unknown"),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", "")
                })
            return {"channel_id": channel_id, "messages": messages}

        elif tool_name == "discord_send_message":
            raw_channel = tool_input.get("channel_id")
            content = tool_input.get("content") or "Greetings from JARVIS! Neural core online, sir."
            channel_id = _resolve_channel_id(token, raw_channel)
            res = _make_discord_request(token, f"channels/{channel_id}/messages", method="POST", body_data={"content": content})
            return {"status": "sent", "channel_id": channel_id, "message_id": res.get("id"), "content": content}

        elif tool_name == "discord_get_user_guilds":
            guilds = _make_discord_request(token, "users/@me/guilds")
            results = [{"id": g.get("id"), "name": g.get("name")} for g in guilds]
            return {"servers": results}

        elif tool_name == "discord_get_guild_channels":
            guild_id = tool_input.get("guild_id")
            channels = _make_discord_request(token, f"guilds/{guild_id}/channels")
            text_channels = [
                {"id": c.get("id"), "name": c.get("name"), "type": c.get("type")}
                for c in channels if c.get("type") == 0
            ]
            return {"guild_id": guild_id, "channels": text_channels}

        else:
            return {"error": f"Unknown Discord tool: {tool_name}"}

    except DiscordAPIError as e:
        print(f"[jarvis] Discord API error on {tool_name}: {e}", file=sys.stderr)
        friendly = {
            401: "Discord rejected the token (401 Unauthorized) — check discord.bot_token in config.json.",
            403: "The bot lacks permission for that channel (403 Forbidden) — check its role/channel permissions.",
            404: "Channel not found (404) — the channel name/ID didn't resolve to a real channel.",
            429: "Discord rate limit hit (429) — too many requests in a short burst; try again in a moment.",
        }.get(e.code, f"Discord API request failed: {e}")
        return {"error": friendly}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"error": f"Discord API request failed: {e}"}

