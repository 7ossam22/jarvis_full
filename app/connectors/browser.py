"""app/connectors/browser.py — system UI and browser control tools (Connector layer).

Lets JARVIS control and interact with the user's on-screen browser:
- browser_open_url: Opens a visible Chromium window/tab on the user's screen.
- browser_click: Clicks any button, link, or element using CSS, text, or ARIA selectors.
- browser_type: Types or fills text into search boxes, forms, and textareas.
- browser_press_key: Presses keyboard keys like Enter, Escape, Tab, Arrow keys.
- browser_scroll: Scrolls the active page up, down, or to a specific element.
- browser_get_content: Reads page text or discovers clickable elements & selectors.
- browser_screenshot: Captures a PNG screenshot of the visible screen/page.
- browser_close: Closes current tab or whole browser.
- browser_list_tabs: Lists open browser tabs.
- system_open: Opens files, folders, or desktop app URLs with xdg-open.

The JARVIS server itself stays standard-library only: Playwright lives in
the daemon's own venv (.venv-browser), reached over localhost HTTP.
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
    """Returns tool definitions for on-screen browser automation and system control."""
    return [
        {
            "name": "browser_open_url",
            "description": (
                "Open a URL in a real, visible browser window on the user's screen "
                "(not an embedded viewer). Use when the user asks to open a website, "
                "video, or page as an actual window they can interact with. Navigates "
                "the current tab by default; set new_tab to true to keep the current page."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to open (e.g. 'https://google.com')."},
                    "new_tab": {
                        "type": "boolean",
                        "description": "Open in a fresh tab instead of reusing the current one (default false).",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "browser_click",
            "description": (
                "Click a clickable element (button, link, input, tab, or menu item) on the active on-screen browser page. "
                "Supports text selectors (e.g. 'text=Search', 'button:has-text(\"Submit\")'), "
                "CSS selectors (e.g. '#search-btn', '.nav-link'), or tag names."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Selector or text of the element to click (e.g. 'text=Log In', '#submit', 'a.pricing').",
                    },
                    "double_click": {
                        "type": "boolean",
                        "description": "Whether to perform a double-click (default false).",
                    },
                },
                "required": ["selector"],
            },
        },
        {
            "name": "browser_type",
            "description": (
                "Type text into an input field, search box, or textarea on the active browser page. "
                "Optionally press Enter immediately after typing to submit a search or form."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Selector of the input or textarea (e.g. 'input[name=\"q\"]', '#search-input', 'textarea').",
                    },
                    "text": {
                        "type": "string",
                        "description": "The text to enter.",
                    },
                    "press_enter": {
                        "type": "boolean",
                        "description": "Whether to press Enter after typing to submit immediately (default false).",
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "Whether to clear existing text before typing (default true).",
                    },
                },
                "required": ["selector", "text"],
            },
        },
        {
            "name": "browser_press_key",
            "description": (
                "Press a keyboard key on the active browser page, such as 'Enter', 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'PageDown', 'Backspace', or 'Space'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key name (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown', 'PageDown', 'Backspace').",
                    },
                    "selector": {
                        "type": "string",
                        "description": "Optional element selector to focus before pressing the key.",
                    },
                },
                "required": ["key"],
            },
        },
        {
            "name": "browser_scroll",
            "description": (
                "Scroll the active browser page up, down, to the top, or to the bottom, or scroll a specific element into view."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "top", "bottom"],
                        "description": "Direction to scroll (default 'down').",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Number of pixels to scroll when direction is 'down' or 'up' (default 500).",
                    },
                    "selector": {
                        "type": "string",
                        "description": "Optional selector of a specific element to scroll into view.",
                    },
                },
            },
        },
        {
            "name": "browser_get_content",
            "description": (
                "Read the content of the currently active browser page. Use 'text' to read clean page text, "
                "or 'elements' to get a list of interactive clickable buttons, links, inputs, and their selectors."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["text", "elements", "html"],
                        "description": "'text' for clean readable text (default), 'elements' for interactive UI elements with selector hints, 'html' for raw HTML.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters of content to return (default 4000).",
                    },
                },
            },
        },
        {
            "name": "browser_screenshot",
            "description": "Capture a screenshot of the visible browser window or full page and save it to disk.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "full_page": {
                        "type": "boolean",
                        "description": "Capture the full scrollable page instead of just the visible viewport (default false).",
                    },
                    "save_path": {
                        "type": "string",
                        "description": "Optional custom file path to save the PNG screenshot.",
                    },
                },
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
    for _ in range(25):
        time.sleep(0.3)
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
        elif tool_name == "browser_click":
            return _daemon_request("/click", {
                "selector": tool_input.get("selector", ""),
                "double_click": bool(tool_input.get("double_click", False)),
            })
        elif tool_name == "browser_type":
            return _daemon_request("/type", {
                "selector": tool_input.get("selector", ""),
                "text": tool_input.get("text", ""),
                "press_enter": bool(tool_input.get("press_enter", False)),
                "clear": bool(tool_input.get("clear", True)),
            })
        elif tool_name == "browser_press_key":
            return _daemon_request("/press_key", {
                "key": tool_input.get("key", "Enter"),
                "selector": tool_input.get("selector", ""),
            })
        elif tool_name == "browser_scroll":
            return _daemon_request("/scroll", {
                "direction": tool_input.get("direction", "down"),
                "amount": tool_input.get("amount", 500),
                "selector": tool_input.get("selector", ""),
            })
        elif tool_name == "browser_get_content":
            return _daemon_request("/content", {
                "mode": tool_input.get("mode", "text"),
                "max_chars": tool_input.get("max_chars", 4000),
            })
        elif tool_name == "browser_screenshot":
            return _daemon_request("/screenshot", {
                "full_page": bool(tool_input.get("full_page", False)),
                "save_path": tool_input.get("save_path", ""),
            })
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

