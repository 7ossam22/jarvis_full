"""app/connectors/browser.py — system UI and browser control tools (Connector layer).

Lets JARVIS control and interact with the user's on-screen browser:
- browser_open_url: Opens or navigates a visible Chromium/Chrome window/tab on the user's screen.
- browser_list_profiles: Lists discovered Chrome profiles on the host machine.
- browser_list_tabs: Lists open browser tabs with their indices, titles, URLs, and active status.
- browser_switch_tab: Switches active view to a specific open tab.
- browser_close: Closes the active tab, a specific tab index, or the entire browser.
- browser_click: Clicks any button, link, or element using CSS, text, or ARIA selectors.
- browser_type: Types or fills text into search boxes, forms, and textareas.
- browser_press_key: Presses keyboard keys like Enter, Escape, Tab, Arrow keys.
- browser_scroll: Scrolls the active page up, down, or to a specific element.
- browser_get_content: Reads page text or discovers clickable elements & selectors.
- browser_screenshot: Captures a PNG screenshot of the visible screen/page.
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
                "Open a URL or search page in a real, visible browser window on the user's screen. "
                "By default, navigates inside the active tab/window (reusing it). "
                "Set new_tab to true ONLY when the user explicitly requests opening in a separate/new tab. "
                "Can specify a persistent user Chrome profile (e.g. 'Hossam', 'Doxx', 'Habiba', 'Elkenany')."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to open (e.g. 'https://google.com', 'https://youtube.com'). Defaults to Google.",
                    },
                    "new_tab": {
                        "type": "boolean",
                        "description": "Open in a fresh tab instead of navigating the current active one (default false).",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Optional Chrome profile name or ID (e.g. 'Hossam', 'Doxx', 'Habiba', 'Elkenany', 'default').",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional specific tab index to navigate in.",
                    },
                },
            },
        },
        {
            "name": "browser_list_profiles",
            "description": "List all discovered Google Chrome profiles on this machine (e.g. Hossam, Doxx, Habiba, Elkenany) with their login emails.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "browser_list_tabs",
            "description": "List all tabs currently open in JARVIS's on-screen browser window, showing their index, title, URL, and which one is active.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "browser_switch_tab",
            "description": "Switch the active browser tab to a specific tab index or by matching a title/URL query.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "0-based index of the tab to switch to (from browser_list_tabs).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Title or URL substring to search for and activate (e.g. 'youtube', 'github').",
                    },
                },
            },
        },
        {
            "name": "browser_close",
            "description": (
                "Close what JARVIS opened on screen: the active browser tab, a specific tab index, "
                "or all tabs and the entire browser window."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["tab", "all", "others"],
                        "description": "'tab' closes the active tab; 'all' closes all tabs and the whole browser window; 'others' closes all tabs except the active one.",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional specific 0-based tab index to close.",
                    },
                },
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
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to click on (defaults to active tab).",
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
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to type on (defaults to active tab).",
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
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to press key on.",
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
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to scroll on.",
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
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to read content from.",
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
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to screenshot.",
                    },
                },
            },
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
                "new_tab": bool(tool_input.get("new_tab", False)),
                "profile": tool_input.get("profile"),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_list_profiles":
            return _daemon_request("/list_profiles")
        elif tool_name == "browser_list_tabs":
            return _daemon_request("/list")
        elif tool_name == "browser_switch_tab":
            return _daemon_request("/switch_tab", {
                "tab_index": tool_input.get("tab_index"),
                "query": tool_input.get("query", ""),
            })
        elif tool_name == "browser_close":
            return _daemon_request("/close", {
                "scope": tool_input.get("scope", "tab"),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_click":
            return _daemon_request("/click", {
                "selector": tool_input.get("selector", ""),
                "double_click": bool(tool_input.get("double_click", False)),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_type":
            return _daemon_request("/type", {
                "selector": tool_input.get("selector", ""),
                "text": tool_input.get("text", ""),
                "press_enter": bool(tool_input.get("press_enter", False)),
                "clear": bool(tool_input.get("clear", True)),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_press_key":
            return _daemon_request("/press_key", {
                "key": tool_input.get("key", "Enter"),
                "selector": tool_input.get("selector", ""),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_scroll":
            return _daemon_request("/scroll", {
                "direction": tool_input.get("direction", "down"),
                "amount": tool_input.get("amount", 500),
                "selector": tool_input.get("selector", ""),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_get_content":
            return _daemon_request("/content", {
                "mode": tool_input.get("mode", "text"),
                "max_chars": tool_input.get("max_chars", 4000),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_screenshot":
            return _daemon_request("/screenshot", {
                "full_page": bool(tool_input.get("full_page", False)),
                "save_path": tool_input.get("save_path", ""),
                "tab_index": tool_input.get("tab_index"),
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


