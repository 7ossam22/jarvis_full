"""app/connectors/browser.py — system UI, browser control, and Flutter Web automation tools (Connector layer).

Lets JARVIS control and interact with the user's on-screen browser and Flutter Web applications:
- browser_open_url: Opens or navigates a visible Chromium/Chrome window/tab on the user's screen.
- browser_list_profiles: Lists discovered Chrome profiles on the host machine.
- browser_list_tabs: Lists open browser tabs with their indices, titles, URLs, and active status.
- browser_switch_tab: Switches active view to a specific open tab.
- browser_close: Closes the active tab, a specific tab index, or the entire browser.
- browser_detect_app_type: Detects whether the active page is Flutter Web or standard HTML web app.
- browser_flutter_get_widgets: Inspects Flutter semantics tree and extracts widgets, labels, and coordinates.
- browser_flutter_click: Clicks a Flutter widget by label, text, tooltip, role, or coordinates on CanvasKit canvas.
- browser_flutter_type: Types text into a Flutter text field on the CanvasKit canvas.
- flutter_run_test: Runs Patrol, Flutter integration tests, or Flutter driver tests via the local Flutter SDK.
- browser_click: Clicks any button, link, or element using CSS, text, or auto-detected Flutter semantics.
- browser_type: Types or fills text into search boxes, forms, textareas, and Flutter text fields.
- browser_press_key: Presses keyboard keys like Enter, Escape, Tab, Arrow keys.
- browser_scroll: Scrolls the active page up, down, or to a specific element.
- browser_get_content: Reads page text or discovers clickable elements / Flutter widgets & selectors.
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
    """Returns tool definitions for on-screen browser automation, Flutter Web, and system control."""
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
                        "description": "The URL to open (e.g. 'https://google.com', 'https://youtube.com', 'http://localhost:8080'). Defaults to Google.",
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
            "name": "browser_detect_app_type",
            "description": (
                "Detect whether the currently active browser page is a Flutter Web application "
                "(rendered via CanvasKit/HTML5 canvas with semantics) or a standard HTML/DOM website. "
                "Returns app type, Flutter renderer, semantics status, and recommended interaction tools."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to inspect (defaults to active tab).",
                    },
                },
            },
        },
        {
            "name": "browser_flutter_get_widgets",
            "description": (
                "Inspect a Flutter Web application's semantics tree to discover interactive Flutter widgets "
                "(buttons, text fields, checkboxes, tabs, list tiles) with their labels, roles, values, and canvas coordinates."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to inspect.",
                    },
                },
            },
        },
        {
            "name": "browser_flutter_click",
            "description": (
                "Click an interactive Flutter widget on a Flutter Web page (CanvasKit canvas). "
                "Target can be a widget label (e.g. 'Login', 'Submit'), tooltip, role, or coordinates 'flutter:coords(x,y)'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Flutter widget label, text, tooltip, role, or coordinate (e.g. 'Login', 'Search', 'role=button', 'coords:250,400').",
                    },
                    "double_click": {
                        "type": "boolean",
                        "description": "Whether to perform a double-click (default false).",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to click on.",
                    },
                },
                "required": ["target"],
            },
        },
        {
            "name": "browser_flutter_type",
            "description": (
                "Type text into a Flutter text field / input on a Flutter Web page (CanvasKit canvas). "
                "Target can be the input label, placeholder, or widget description."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Flutter text field label, placeholder, or selector (e.g. 'Email', 'Password', 'Search', 'Username').",
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
                        "description": "Optional tab index to type on.",
                    },
                },
                "required": ["target", "text"],
            },
        },
        {
            "name": "flutter_run_test",
            "description": (
                "Run a Patrol test, Flutter integration test, or Flutter driver test on a Flutter project using the host's Flutter SDK."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "test_type": {
                        "type": "string",
                        "enum": ["integration_test", "patrol", "flutter_drive", "unit_test"],
                        "description": "Type of test to execute (default 'integration_test').",
                    },
                    "target": {
                        "type": "string",
                        "description": "Target test file path (e.g. 'integration_test/app_test.dart', 'patrol_test/flow_test.dart').",
                    },
                    "device": {
                        "type": "string",
                        "description": "Target device (e.g. 'chrome', 'linux', 'android', 'web-server'). Default 'chrome'.",
                    },
                    "project_path": {
                        "type": "string",
                        "description": "Optional absolute path to the Flutter project directory.",
                    },
                    "extra_args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional additional command line arguments.",
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
                "Auto-detects Flutter Web vs standard HTML DOM and clicks Flutter widgets or DOM elements accordingly."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Selector, text, or Flutter label of the element to click (e.g. 'text=Log In', '#submit', 'Submit', 'flutter:label(\"Submit\")').",
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
                "Type text into an input field, search box, textarea, or Flutter text field on the active browser page. "
                "Auto-detects Flutter Web vs standard HTML DOM."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Selector or Flutter field label (e.g. 'input[name=\"q\"]', '#search-input', 'Search', 'Email').",
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
            "name": "browser_batch_actions",
            "description": (
                "FAST PATH for form filling: execute MANY browser/Flutter actions sequentially in ONE call — "
                "clicks, typing, key presses, scrolls, file uploads — then get back a fresh Flutter widget list "
                "in the same response. Use this instead of separate browser_flutter_click / browser_flutter_type / "
                "browser_scroll calls whenever you already know every step (e.g. answering all visible form "
                "questions top-to-bottom and scrolling). Each action is an object with 'cmd' plus that command's "
                "normal parameters. Available cmds: 'flutter_click' (target), 'flutter_type' (target, text), "
                "'click' (selector), 'type' (selector, text), 'press_key' (key), 'scroll' (direction, amount), "
                "'upload_file' (file_path). Actions run strictly in order; on a failed click/type the batch stops "
                "and returns fresh widgets so you can re-plan. The response's 'widgets' list reflects the page "
                "AFTER all actions ran — use it directly instead of calling browser_flutter_get_widgets again."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "description": "Ordered list of actions to execute.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "cmd": {
                                    "type": "string",
                                    "enum": ["flutter_click", "flutter_type", "click", "type", "press_key", "scroll", "upload_file"],
                                    "description": "Which action to run.",
                                },
                                "target": {"type": "string", "description": "Flutter widget target for flutter_click/flutter_type, e.g. 'flutter:coords(850,420)' or a label."},
                                "selector": {"type": "string", "description": "CSS selector for click/type on standard DOM pages."},
                                "text": {"type": "string", "description": "Text to type for flutter_type/type."},
                                "key": {"type": "string", "description": "Key for press_key, e.g. 'Enter', 'Tab'."},
                                "direction": {"type": "string", "description": "Scroll direction: down, up, top, bottom."},
                                "amount": {"type": "integer", "description": "Scroll amount in pixels (default 500)."},
                                "file_path": {"type": "string", "description": "Absolute file path for upload_file."},
                            },
                            "required": ["cmd"],
                        },
                    },
                    "return_widgets": {
                        "type": "boolean",
                        "description": "Include a fresh Flutter widget list in the response (default true).",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to act on.",
                    },
                },
                "required": ["actions"],
            },
        },
        {
            "name": "browser_scroll",
            "description": (
                "Scroll the active browser page up, down, to the top, or to the bottom, or scroll a specific element / Flutter widget into view."
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
                        "description": "Optional selector of a specific element or Flutter widget to scroll into view.",
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
                "'elements' to get interactive UI elements (or Flutter widgets if Flutter Web is running), "
                "or 'flutter_widgets' to explicitly extract the Flutter semantics widget tree."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["text", "elements", "flutter_widgets", "html"],
                        "description": "'text' for clean readable text (default), 'elements' for interactive UI elements / Flutter widgets, 'flutter_widgets' for Flutter widget tree, 'html' for raw HTML.",
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
            "name": "browser_upload_file",
            "description": (
                "Upload a local file into a file upload field/picker on the active browser page. "
                "Works with Flutter Web file pickers, file chooser dialogs, and HTML file inputs."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the local file to upload (e.g. '/path/to/Informed_Consent Template.pdf').",
                    },
                    "selector": {
                        "type": "string",
                        "description": "Optional selector or Flutter label of the upload button/field to click.",
                    },
                    "tab_index": {
                        "type": "integer",
                        "description": "Optional tab index to upload on.",
                    },
                },
                "required": ["file_path"],
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


def _daemon_request(path, payload=None, timeout=65):
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
    """Executes a browser, Flutter automation, or system UI tool call."""
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
        elif tool_name == "browser_detect_app_type":
            return _daemon_request("/detect_app_type", {
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_flutter_get_widgets":
            return _daemon_request("/flutter_widgets", {
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_flutter_click":
            return _daemon_request("/flutter_click", {
                "target": tool_input.get("target", ""),
                "double_click": bool(tool_input.get("double_click", False)),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "browser_flutter_type":
            return _daemon_request("/flutter_type", {
                "target": tool_input.get("target", ""),
                "text": tool_input.get("text", ""),
                "press_enter": bool(tool_input.get("press_enter", False)),
                "clear": bool(tool_input.get("clear", True)),
                "tab_index": tool_input.get("tab_index"),
            })
        elif tool_name == "flutter_run_test":
            return _daemon_request("/flutter_run_test", {
                "test_type": tool_input.get("test_type", "integration_test"),
                "target": tool_input.get("target", ""),
                "device": tool_input.get("device", "chrome"),
                "project_path": tool_input.get("project_path", ""),
                "extra_args": tool_input.get("extra_args", []),
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
        elif tool_name == "browser_batch_actions":
            # A batch runs many sequential page actions — give it far longer
            # than the single-action timeout before declaring it dead.
            return _daemon_request("/batch", {
                "actions": tool_input.get("actions") or [],
                "return_widgets": tool_input.get("return_widgets", True),
                "tab_index": tool_input.get("tab_index"),
            }, timeout=240)
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
        elif tool_name == "browser_upload_file":
            return _daemon_request("/upload_file", {
                "file_path": tool_input.get("file_path", ""),
                "selector": tool_input.get("selector", ""),
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



