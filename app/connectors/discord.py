"""app/connectors/discord.py — Discord integration & tool definitions (Connector layer).

Provides standard Discord tools (discord_get_recent_messages, discord_send_message,
discord_get_user_guilds, discord_get_guild_channels) interfacing directly with the
Discord REST API (v10). Standard library only — zero pip dependencies.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DISCORD_API_BASE = "https://discord.com/api/v10"


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


def _make_discord_request(token, endpoint, params=None, method="GET", body_data=None):
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

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def execute_discord_tool(cfg, tool_name, tool_input):
    """Executes a Discord tool call using configured Bot Token or User Token."""
    token = cfg.get("discord.bot_token") or cfg.get("discord.token") or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        return {
            "error": "Discord token not configured. Please set 'discord.bot_token' in config.json."
        }

    try:
        if tool_name == "discord_get_recent_messages":
            channel_id = tool_input.get("channel_id")
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
            channel_id = tool_input.get("channel_id")
            content = tool_input.get("content")
            res = _make_discord_request(token, f"channels/{channel_id}/messages", method="POST", body_data={"content": content})
            return {"status": "sent", "message_id": res.get("id")}

        elif tool_name == "discord_get_user_guilds":
            guilds = _make_discord_request(token, "users/@me/guilds")
            results = [{"id": g.get("id"), "name": g.get("name")} for g in guilds]
            return {"servers": results}

        elif tool_name == "discord_get_guild_channels":
            guild_id = tool_input.get("guild_id")
            channels = _make_discord_request(token, f"guilds/{guild_id}/channels")
            # Filter to text channels (type 0)
            text_channels = [
                {"id": c.get("id"), "name": c.get("name"), "type": c.get("type")}
                for c in channels if c.get("type") == 0
            ]
            return {"guild_id": guild_id, "channels": text_channels}

        else:
            return {"error": f"Unknown Discord tool: {tool_name}"}

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return {"error": f"Discord API request failed: {e}"}
