"""app/connectors — external-service tools (Connector layer).

`registry` is the single source of truth: every tool below is registered on it
at import time, and providers go through it for both the tool list and dispatch
(see registry.py). The individual `get_*_tools` / `execute_*_tool` pairs stay
exported for direct use — app/http_server.py's POST /jira/action calls
execute_jira_tool without a model in the loop — but new code should prefer the
registry so provider access control is enforced.
"""
from .registry import Tool, ToolRegistry, ToolRegistryError, registry
from .gmail import get_gmail_tools, execute_gmail_tool
from .discord import get_discord_tools, execute_discord_tool
from .browser import get_browser_tools, execute_browser_tool
from .system import get_system_tools, execute_system_tool
from .jira import get_jira_tools, execute_jira_tool
from .spotify import get_spotify_tools, execute_spotify_tool

__all__ = [
    "Tool", "ToolRegistry", "ToolRegistryError", "registry",
    "get_gmail_tools", "execute_gmail_tool",
    "get_discord_tools", "execute_discord_tool",
    "get_browser_tools", "execute_browser_tool",
    "get_system_tools", "execute_system_tool",
    "get_jira_tools", "execute_jira_tool",
    "get_spotify_tools", "execute_spotify_tool",
]

