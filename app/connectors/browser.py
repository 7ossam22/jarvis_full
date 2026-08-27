"""app/connectors/browser.py — system UI control tools (Connector layer).

Lets JARVIS open and close things on the user's actual screen:
- browser_open_url / browser_close / browser_list_tabs drive a real visible
  Chromium window through the Playwright daemon in tools/browser_daemon.py
  (auto-started on first use from .venv-browser — see tools/setup_browser.sh).
- system_open hands anything else (a file, folder, or app-registered URL
  scheme) to xdg-open.

The JARVIS server itself stays standard-library only: Playwright lives in
the daemon's own venv, reached over localhost HTTP, the same way the claude
CLI is an external helper.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DAEMON_URL = "http://127.0.0.1:4701"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAEMON_SCRIPT = os.path.join(ROOT, "tools", "browser_daemon.py")
VENV_PYTHON = os.path.join(ROOT, ".venv-browser", "bin", "python")
DAEMON_LOG = os.path.join(ROOT, "tools", "browser_daemon.log")


def get_browser_tools():
    """Returns Anthropic API tool definitions for on-screen browser/system control."""
    return [
        {
            "name": "browser_open_url",
            "description": (
                "Open a URL in a real, visible browser window on the user's screen "
                "(not an embedded viewer). Use when the user asks to open a website, "
                "video, or page as an actual window they can interact with. Navigates "
                "the current tab by default; set new_tab only when the user explicitly "
                "asks for a new/another tab or to keep the current page."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to open."},
                    "new_tab": {
                        "type": "boolean",
                        "description": "Open in a fresh tab instead of reusing the current one (default false).",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "browser_close",
            "description": (
                "Close what JARVIS opened on screen: the most recent browser tab, "
                "or the whole browser window."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["tab", "all"],
                        "description": "'tab' closes the latest tab (default); 'all' closes the whole browser.",
                    },
                },
            },
        },
        {
            "name": "browser_list_tabs",
            "description": "List the tabs currently open in JARVIS's on-screen browser window.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "system_open",
            "description": (
                "Open a local file, folder, or application on the user's machine via "
                "the desktop's default handler (xdg-open). For websites prefer "
                "browser_open_url."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Path or URI to open, e.g. '/home/user/Pictures' or 'mailto:'.",
                    },
                },
                "required": ["target"],
            },
        },
    ]


def _daemon_request(path, payload=None, timeout=35):
    data = json.dumps(payload or {}).encode("utf-8") if path != "/health" else None
    req = urllib.request.Request(
        f"{DAEMON_URL}{path}",
        data=data,
        method="GET" if path == "/health" else "POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _daemon_alive():
    try:
        return bool(_daemon_request("/health", timeout=2).get("ok"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _ensure_daemon():
    """Starts the Playwright daemon if it isn't running. Returns an error
    string, or None when the daemon is reachable."""
    if _daemon_alive():
        return None
    if not os.path.exists(VENV_PYTHON):
        return (
            "The browser control environment isn't installed yet — run "
            "tools/setup_browser.sh once to set it up."
        )
    with open(DAEMON_LOG, "a") as log:
        subprocess.Popen(
            [VENV_PYTHON, DAEMON_SCRIPT],
            stdout=log, stderr=log,
            start_new_session=True,
        )
    for _ in range(20):
        time.sleep(0.4)
        if _daemon_alive():
            return None
    return f"The browser daemon failed to start — see {DAEMON_LOG}."


def execute_browser_tool(cfg, tool_name, tool_input):
    """Executes a browser/system UI tool call."""
    try:
        if tool_name == "system_open":
            target = (tool_input.get("target") or "").strip()
            if not target:
                return {"error": "no target given"}
            subprocess.Popen(
                ["xdg-open", target],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"status": "opened", "target": target}

        err = _ensure_daemon()
        if err:
            return {"error": err}

        if tool_name == "browser_open_url":
            return _daemon_request("/open", {
                "url": tool_input.get("url", ""),
                "new_tab": bool(tool_input.get("new_tab")),
            })
        elif tool_name == "browser_close":
            return _daemon_request("/close", {"scope": tool_input.get("scope", "tab")})
        elif tool_name == "browser_list_tabs":
            return _daemon_request("/list")
        else:
            return {"error": f"Unknown browser tool: {tool_name}"}

    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", str(e))
        except Exception:
            detail = str(e)
        print(f"[jarvis] browser tool {tool_name} failed: {detail}", file=sys.stderr)
        return {"error": detail}
    except (urllib.error.URLError, TimeoutError, OSError, FileNotFoundError) as e:
        print(f"[jarvis] browser tool {tool_name} failed: {e}", file=sys.stderr)
        return {"error": f"browser control failed: {e}"}
