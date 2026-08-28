# Discord Connector

The **Discord Connector** allows JARVIS to communicate through Discord servers, read channel conversations, and post text messages and image/screenshot attachments.

## Capabilities

- **Read Channel Messages** (`discord_get_recent_messages`): Fetches recent messages from any authorized Discord text channel for context, summaries, or notifications.
- **Send Text & Image Messages** (`discord_send_message`): Posts messages to Discord channels. Supports binary multipart file uploads via `file_path` to upload desktop or browser screenshots directly as Discord image attachments.
- **Server & Channel Discovery** (`discord_get_user_guilds`, `discord_get_guild_channels`): Lists available Discord guilds (servers) and text channels accessible by the bot.

## Configuration

Configured in `config.json`:
```json
{
  "discord": {
    "bot_token": "MTU0MTk4MjA0..."
  }
}
```

## Related Systems

- [[Playwright Browser Control]] and [[Linux System Controller]] for capturing screenshots sent to Discord.
- [[Gmail Connector]] for relaying email updates to Discord channels.
- [[Atlassian Jira Connector]] for posting Jira ticket status notifications.
