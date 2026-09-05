"""app/providers/cli_provider.py — Local CLI LLM backends & fallback runner.

Provides unified execution for command-line LLM tools:
  - "agy": Antigravity CLI (`agy -p`)
  - "claude": Claude Code CLI (`claude -p`)

Used as:
  1. A fallback mechanism when direct HTTP APIs (Anthropic, Gemini) are
     unconfigured or encounter errors/rate limits/quotas.
  2. First-class providers selectable via `model.provider` in config.json
     ("agy" | "claude").

Configured via `model.cli_fallback` in config.json:
  - "agy" | "antigravity": Always use Antigravity CLI as fallback.
  - "claude": Always use Claude Code CLI as fallback.
  - "auto" (default): Intelligently choose based on the active provider
    and installed CLIs, with failover between them if one fails (e.g. session limits).
  - "none" | "off": Disable CLI fallback entirely.
"""
import json
import shutil
import subprocess
import sys

from .. import vision
from ..connectors.registry import ANTHROPIC, GEMINI, registry
from .llm import LLMProvider

CLI_AGY = "agy"
CLI_CLAUDE = "claude"
CLI_AUTO = "auto"
CLI_NONE = "none"

KNOWN_CLIS = (CLI_AGY, CLI_CLAUDE)

# Cheap pre-filter: only run the (slow) LLM tool-decision call when the recent
# conversation plausibly involves a connector at all.
CONNECTOR_HINTS = (
    "discord", "email", "gmail", "mail", "inbox", "send", "post",
    "share", "message", "dm", "channel",
    "open", "close", "browser", "tab", "tabs", "website", "launch", "chrome",
    "profile", "profiles", "switch", "click", "type", "scroll", "screenshot",
    "jira", "ticket", "issue", "volume", "sound", "stats", "app",
    "flutter", "patrol", "canvaskit", "widget", "widgets", "semantics",
)


def normalize_cli_name(name: str | None) -> str:
    """Normalizes CLI identifiers and aliases."""
    if not name:
        return ""
    cleaned = str(name).strip().lower()
    if cleaned in ("antigravity", "gemini-cli"):
        return CLI_AGY
    if cleaned in ("claude", "anthropic", "claude-code"):
        return CLI_CLAUDE
    if cleaned in ("off", "disabled", "false", "no"):
        return CLI_NONE
    return cleaned


def is_cli_installed(cli_name: str) -> bool:
    """Checks whether the requested CLI binary is found on PATH."""
    cmd = CLI_AGY if cli_name == "antigravity" else cli_name
    return shutil.which(cmd) is not None


def get_configured_cli_choice(cfg) -> str:
    """Reads the user's chosen CLI fallback mode from config.json."""
    if not cfg:
        return CLI_AUTO
    raw = cfg.get("model.cli_fallback") or cfg.get("model.fallback_cli") or CLI_AUTO
    return normalize_cli_name(raw)


def resolve_cli_candidates(cfg, preferred: str | None = None) -> list[str]:
    """Returns an ordered list of CLI tools to try, taking into account user
    configuration, the calling provider's preference, and available binaries."""
    choice = get_configured_cli_choice(cfg)

    if choice == CLI_NONE:
        return []

    preferred_norm = normalize_cli_name(preferred)

    if choice in KNOWN_CLIS:
        candidates = []
        if is_cli_installed(choice):
            candidates.append(choice)
        # Automatic backup failover to the other CLI if installed
        other = CLI_CLAUDE if choice == CLI_AGY else CLI_AGY
        if is_cli_installed(other):
            candidates.append(other)
        return candidates

    # "auto" or unspecified: order by preference, then other installed
    first = preferred_norm if preferred_norm in KNOWN_CLIS else None
    if not first:
        # Default system priority: agy if available, else claude
        first = CLI_AGY if is_cli_installed(CLI_AGY) else CLI_CLAUDE
    second = CLI_CLAUDE if first == CLI_AGY else CLI_AGY

    candidates = []
    if is_cli_installed(first):
        candidates.append(first)
    if is_cli_installed(second):
        candidates.append(second)
    return candidates


def is_cli_available(cfg=None, preferred: str | None = None) -> bool:
    """True if at least one usable CLI tool is available under current configuration."""
    return bool(resolve_cli_candidates(cfg, preferred=preferred))


def _decide_connector_action(cfg, messages, cli_name: str, provider_name: str = ANTHROPIC):
    """Prompts the CLI to decide whether connector tools are required.
    Returns parsed JSON dict or None."""
    transcript = "\n\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages[-6:]
    )
    tools_desc = json.dumps(registry.get_tools_for_provider(provider_name))
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

    if cli_name == CLI_CLAUDE:
        cmd = ["claude", "-p", transcript, "--system-prompt", sys_p, "--dangerously-skip-permissions"]
    else:  # CLI_AGY
        combined_prompt = f"{sys_p}\n\nCONVERSATION:\n{transcript}"
        cmd = ["agy", "-p", combined_prompt, "--disable-slash-commands", "--effort", "low", "--dangerously-skip-permissions"]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=45,
            stdin=subprocess.DEVNULL,
        )
        out = res.stdout.strip()
        if "{" in out and "}" in out:
            parsed = json.loads(out[out.find("{"):out.rfind("}")+1])
            if parsed.get("tool") and parsed["tool"] != "none":
                return parsed
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as e:
        print(f"[jarvis] connector decision failed on {cli_name} ({e})", file=sys.stderr)
    return None


def call_cli_turn(cfg, system_prompt: str, messages: list, cli_name: str,
                  provider_name: str = ANTHROPIC) -> str:
    """Executes a single conversational turn using the specified CLI binary."""
    # Defend against sending images to text-only CLIs
    if vision.has_images(messages):
        raise RuntimeError(
            f"the {cli_name} CLI fallback is text-only and cannot see an image; "
            "set model.provider_api_key to use the vision-capable API"
        )

    extra_context = ""
    recent_text = " ".join(str(m.get("content", "")) for m in messages[-5:]).lower()
    if any(k in recent_text for k in CONNECTOR_HINTS):
        action = _decide_connector_action(cfg, messages, cli_name=cli_name, provider_name=provider_name)
        if action:
            tool_name = action.get("tool", "")
            tool_input = action.get("input") or {}
            result = registry.dispatch(tool_name, tool_input, provider_name, cfg)
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

    if cli_name == CLI_CLAUDE:
        cmd = ["claude", "-p", full_prompt, "--system-prompt", system_prompt, "--dangerously-skip-permissions"]
    else:  # CLI_AGY
        prompt = f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nCONVERSATION:\n{full_prompt}"
        effort = (cfg.get("model.agy_effort") if cfg else None) or "low"
        cmd = ["agy", "-p", prompt, "--disable-slash-commands", "--effort", effort, "--dangerously-skip-permissions"]
        model_id = cfg.get("model.agy_model") if cfg else None
        if model_id:
            cmd.extend(["--model", model_id])

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=90,
        stdin=subprocess.DEVNULL,
    )

    if result.returncode != 0 or not result.stdout.strip():
        err = (result.stderr or "").strip() or (result.stdout or "").strip() or f"{cli_name} CLI returned no output"
        raise RuntimeError(err)

    return result.stdout.strip()


def call_cli_fallback(cfg, system_prompt: str, messages: list,
                      preferred: str | None = None,
                      provider_name: str = ANTHROPIC) -> str:
    """Attempts execution across resolved CLI candidates in order."""
    candidates = resolve_cli_candidates(cfg, preferred=preferred)
    if not candidates:
        raise RuntimeError(
            "No CLI fallback is available: neither `agy` nor `claude` CLI could be used "
            "under current configuration."
        )

    last_error = None
    for i, cli in enumerate(candidates):
        try:
            return call_cli_turn(cfg, system_prompt, messages, cli_name=cli, provider_name=provider_name)
        except Exception as e:
            last_error = e
            if i + 1 < len(candidates):
                next_cli = candidates[i + 1]
                print(f"[jarvis] {cli} CLI call failed ({e}); failing over to {next_cli} CLI…", file=sys.stderr)
            else:
                print(f"[jarvis] {cli} CLI call failed ({e})", file=sys.stderr)

    raise RuntimeError(f"All CLI fallback candidates failed. Last error: {last_error}")


class CLIProvider(LLMProvider):
    """First-class LLMProvider backed directly by a local CLI tool."""

    def __init__(self, cfg, cli_type: str = CLI_AGY):
        super().__init__(cfg)
        self.cli_type = normalize_cli_name(cli_type)
        self.name = self.cli_type

    def is_configured(self) -> bool:
        return is_cli_installed(self.cli_type)

    def supports_vision(self) -> bool:
        return False

    def converse(self, system_prompt: str, messages: list) -> str:
        provider_name = GEMINI if self.cli_type == CLI_AGY else ANTHROPIC
        return call_cli_fallback(
            self._cfg, system_prompt, messages, preferred=self.cli_type, provider_name=provider_name
        )
