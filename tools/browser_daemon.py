#!/usr/bin/env python3
"""tools/browser_daemon.py — JARVIS's hands on a real browser (Playwright).

Runs OUTSIDE the stdlib-only JARVIS server because it needs the playwright
pip package — launch it with .venv-browser/bin/python (see
tools/setup_browser.sh). The JARVIS server's browser connector
(app/connectors/browser.py) starts this on demand and talks to it over
http://127.0.0.1:4701.

Features:
- Full machine & tab state awareness (total tabs, active tab index, titles & URLs).
- Persistent profile support preserving user logins, cookies, and bookmarks.
- Tab management: tab reuse, new tab creation, tab listing, tab switching, tab closing.
- Clean process lifecycle: prevents orphaned Chromium processes and zombie windows.
- Non-destructive error handling: element timeouts no longer discard the live browser window.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

PORT = 4701
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

state = {
    "pw": None,
    "browser": None,
    "context": None,
    "current_profile": None,
    "active_tab_index": 0,
}


def _clean_orphaned_playwright_processes():
    """Kills any stray Playwright temporary Chromium processes to prevent clutter."""
    try:
        subprocess.run(
            ["pkill", "-f", "playwright_chromiumdev_profile"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        pass


def get_chrome_profiles():
    """Scans ~/.config/google-chrome and ~/.config/chromium for real user profiles."""
    profiles = []
    base_dirs = [
        os.path.expanduser("~/.config/google-chrome"),
        os.path.expanduser("~/.config/chromium"),
    ]
    seen_ids = set()
    for base in base_dirs:
        local_state_path = os.path.join(base, "Local State")
        if not os.path.exists(local_state_path):
            continue
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            info_cache = data.get("profile", {}).get("info_cache", {})
            for profile_id, info in info_cache.items():
                name = info.get("name", profile_id)
                user_name = info.get("user_name", "")
                slug = name.lower().replace(" ", "_")
                if slug in seen_ids:
                    continue
                seen_ids.add(slug)
                profiles.append({
                    "id": profile_id,
                    "name": name,
                    "user_name": user_name,
                    "slug": slug,
                    "source_dir": os.path.join(base, profile_id),
                    "browser": "google-chrome" if "google-chrome" in base else "chromium",
                })
        except Exception as e:
            print(f"[browser-daemon] error reading profiles from {local_state_path}: {e}", file=sys.stderr)

    # Check jarvis-chrome profiles directory
    jarvis_base = os.path.expanduser("~/.config/jarvis-chrome")
    if os.path.isdir(jarvis_base):
        for entry in os.listdir(jarvis_base):
            entry_path = os.path.join(jarvis_base, entry)
            if os.path.isdir(entry_path) and entry not in seen_ids:
                profiles.append({
                    "id": entry,
                    "name": entry.replace("_", " ").title(),
                    "user_name": "",
                    "slug": entry.lower(),
                    "source_dir": entry_path,
                    "browser": "jarvis-persistent",
                })
                seen_ids.add(entry.lower())

    return profiles


def _resolve_profile_dir(profile_query):
    """Maps a profile name/query to a safe persistent directory in ~/.config/jarvis-chrome/."""
    jarvis_base = os.path.expanduser("~/.config/jarvis-chrome")
    os.makedirs(jarvis_base, exist_ok=True)

    if not profile_query or profile_query.strip().lower() in ("default", "none", ""):
        profile_slug = "default"
        target_dir = os.path.join(jarvis_base, "default")
        os.makedirs(target_dir, exist_ok=True)
        return target_dir, "default"

    query_clean = profile_query.strip().lower()
    discovered = get_chrome_profiles()

    # Exact or substring match
    matched = None
    for p in discovered:
        if (
            p["slug"] == query_clean
            or p["name"].lower() == query_clean
            or p["id"].lower() == query_clean
            or query_clean in p["name"].lower()
        ):
            matched = p
            break

    if matched:
        slug = re.sub(r"[^a-z0-9_]", "_", matched["name"].lower())
        target_dir = os.path.join(jarvis_base, f"profile_{slug}")
        os.makedirs(target_dir, exist_ok=True)
        return target_dir, matched["name"]

    # Fallback to sanitized custom slug
    slug = re.sub(r"[^a-z0-9_]", "_", query_clean)
    target_dir = os.path.join(jarvis_base, f"profile_{slug}")
    os.makedirs(target_dir, exist_ok=True)
    return target_dir, profile_query


def get_open_pages():
    """Returns all open, non-closed pages in the current browser context."""
    if state["context"] is None:
        return []
    try:
        pages = []
        for p in state["context"].pages:
            try:
                if not p.is_closed():
                    pages.append(p)
            except Exception:
                pass
        return pages
    except Exception:
        return []


def ensure_browser(profile=None):
    """Ensures Playwright browser context is active, healthy, and on the requested profile."""
    req_dir, req_profile_name = _resolve_profile_dir(profile)

    # Check if context already running for this profile
    if (
        state["context"] is not None
        and state["current_profile"] == req_profile_name
    ):
        pages = get_open_pages()
        if pages:
            return  # Context is healthy with open pages
        try:
            # If all pages were closed, create a fresh page
            state["context"].new_page()
            state["active_tab_index"] = 0
            return
        except Exception:
            # Context is dead, fall through to re-launch
            state["context"] = None

    # If another profile was open, close it cleanly
    if state["context"] is not None and state["current_profile"] != req_profile_name:
        try:
            state["context"].close()
        except Exception:
            pass
        state["context"] = None

    if state["pw"] is None:
        state["pw"] = sync_playwright().start()

    # Launch persistent context preserving cookies/logins without conflicts
    launch_args = [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-infobars",
    ]

    context = None
    try:
        # Try real Google Chrome channel first
        context = state["pw"].chromium.launch_persistent_context(
            user_data_dir=req_dir,
            channel="chrome",
            headless=False,
            args=launch_args,
            no_viewport=True,
        )
    except Exception as e:
        print(f"[browser-daemon] Chrome channel launch failed ({e}); trying default Chromium…", file=sys.stderr)
        try:
            context = state["pw"].chromium.launch_persistent_context(
                user_data_dir=req_dir,
                headless=False,
                args=launch_args,
                no_viewport=True,
            )
        except Exception as e2:
            print(f"[browser-daemon] Persistent launch failed ({e2}); falling back to ephemeral launch…", file=sys.stderr)
            state["browser"] = state["pw"].chromium.launch(
                headless=False,
                args=launch_args,
            )
            context = state["browser"].new_context(no_viewport=True)

    state["context"] = context
    state["current_profile"] = req_profile_name
    state["active_tab_index"] = 0

    # Ensure at least 1 page exists
    pages = get_open_pages()
    if not pages:
        context.new_page()


def build_tab_list():
    """Returns structured metadata for all open tabs."""
    pages = get_open_pages()
    active_idx = max(0, min(state.get("active_tab_index", 0), len(pages) - 1)) if pages else 0
    tabs = []
    for i, p in enumerate(pages):
        try:
            title = p.title() or "Untitled"
            url = p.url or "about:blank"
        except Exception:
            title, url = "Tab", ""
        tabs.append({
            "index": i,
            "title": title,
            "url": url,
            "is_active": (i == active_idx),
        })
    return tabs


def get_active_page(tab_index=None, profile=None):
    """Retrieves the active page (or specific tab_index) and brings it to front."""
    ensure_browser(profile=profile)
    pages = get_open_pages()
    if not pages:
        page = state["context"].new_page()
        pages = [page]

    if tab_index is not None:
        try:
            idx = int(tab_index)
            if 0 <= idx < len(pages):
                state["active_tab_index"] = idx
            elif -len(pages) <= idx < 0:
                state["active_tab_index"] = len(pages) + idx
        except (ValueError, TypeError):
            pass

    idx = max(0, min(state.get("active_tab_index", 0), len(pages) - 1))
    state["active_tab_index"] = idx
    page = pages[idx]
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


def cmd_list_profiles(_payload):
    """Lists all available Chrome profiles on the host."""
    profiles = get_chrome_profiles()
    return {
        "active_profile": state.get("current_profile", "default"),
        "total_profiles": len(profiles),
        "profiles": profiles,
    }


def cmd_open(payload):
    """Opens a URL, navigating in the active tab or opening a new tab."""
    url = (payload.get("url") or "").strip()
    profile = payload.get("profile")
    new_tab = bool(payload.get("new_tab", False))
    tab_index = payload.get("tab_index")

    if not url:
        url = "https://www.google.com"
    elif not url.startswith(("http://", "https://", "file://", "about:", "chrome://")):
        url = "https://" + url

    ensure_browser(profile=profile)
    pages = get_open_pages()

    if new_tab:
        page = state["context"].new_page()
        pages = get_open_pages()
        state["active_tab_index"] = len(pages) - 1
    else:
        if tab_index is not None:
            page = get_active_page(tab_index=tab_index)
        elif len(pages) == 1 and pages[0].url in ("about:blank", "chrome://newtab/"):
            page = pages[0]
            state["active_tab_index"] = 0
        else:
            page = get_active_page()

    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.bring_to_front()

    all_tabs = build_tab_list()
    return {
        "status": "opened",
        "url": page.url,
        "title": page.title(),
        "profile": state.get("current_profile", "default"),
        "active_tab_index": state["active_tab_index"],
        "total_tabs": len(all_tabs),
        "tabs": all_tabs,
    }


def cmd_list(_payload):
    """Lists all open tabs in the controlled browser window."""
    if state["context"] is None:
        return {
            "status": "no_browser_open",
            "total_tabs": 0,
            "active_tab_index": None,
            "profile": None,
            "tabs": [],
        }

    pages = get_open_pages()
    if not pages:
        return {
            "status": "no_tabs_open",
            "total_tabs": 0,
            "active_tab_index": None,
            "profile": state.get("current_profile", "default"),
            "tabs": [],
        }

    tabs = build_tab_list()
    return {
        "status": "ok",
        "total_tabs": len(tabs),
        "active_tab_index": state.get("active_tab_index", 0),
        "profile": state.get("current_profile", "default"),
        "tabs": tabs,
    }


def cmd_switch_tab(payload):
    """Switches the active tab by tab index or matching title/URL query."""
    if state["context"] is None:
        return {"error": "No browser is currently open."}

    pages = get_open_pages()
    if not pages:
        return {"error": "No open tabs to switch to."}

    target_idx = None
    if "tab_index" in payload and payload["tab_index"] is not None:
        try:
            idx = int(payload["tab_index"])
            if 0 <= idx < len(pages):
                target_idx = idx
            elif -len(pages) <= idx < 0:
                target_idx = len(pages) + idx
        except (ValueError, TypeError):
            pass

    query = (payload.get("query") or "").strip().lower()
    if target_idx is None and query:
        for i, p in enumerate(pages):
            try:
                if query in (p.title() or "").lower() or query in (p.url or "").lower():
                    target_idx = i
                    break
            except Exception:
                pass

    if target_idx is None:
        return {
            "error": f"Tab not found matching {payload}. Total open tabs: {len(pages)}.",
            "tabs": build_tab_list(),
        }

    page = get_active_page(tab_index=target_idx)
    page.bring_to_front()

    tabs = build_tab_list()
    return {
        "status": "switched",
        "active_tab_index": state["active_tab_index"],
        "title": page.title(),
        "url": page.url,
        "total_tabs": len(tabs),
        "tabs": tabs,
    }


def cmd_close(payload):
    """Closes tabs or the entire browser window cleanly."""
    scope = (payload.get("scope") or "tab").lower()
    tab_index = payload.get("tab_index")

    if state["context"] is None:
        _clean_orphaned_playwright_processes()
        return {
            "status": "nothing_open",
            "message": "No browser was open.",
            "remaining_tabs_count": 0,
            "tabs": [],
        }

    pages = get_open_pages()

    if scope in ("all", "browser") or not pages:
        # Close all pages and context cleanly
        for p in pages:
            try:
                p.close()
            except Exception:
                pass
        try:
            state["context"].close()
        except Exception:
            pass
        if state["browser"] is not None:
            try:
                state["browser"].close()
            except Exception:
                pass

        state["context"] = None
        state["browser"] = None
        state["current_profile"] = None
        state["active_tab_index"] = 0
        _clean_orphaned_playwright_processes()

        return {
            "status": "browser_closed",
            "message": "All browser tabs and windows have been closed.",
            "remaining_tabs_count": 0,
            "tabs": [],
        }

    if scope == "others":
        active_p = get_active_page()
        for p in pages:
            if p != active_p:
                try:
                    p.close()
                except Exception:
                    pass
        state["active_tab_index"] = 0
        remaining = get_open_pages()
        return {
            "status": "other_tabs_closed",
            "message": "Closed all tabs except the active tab.",
            "remaining_tabs_count": len(remaining),
            "active_tab_index": 0,
            "tabs": build_tab_list(),
        }

    # scope == "tab" (single tab close)
    target_page = None
    if tab_index is not None:
        try:
            idx = int(tab_index)
            if 0 <= idx < len(pages):
                target_page = pages[idx]
        except (ValueError, TypeError):
            pass

    if target_page is None:
        target_page = get_active_page()

    closed_title = target_page.title() if not target_page.is_closed() else ""
    closed_url = target_page.url if not target_page.is_closed() else ""

    try:
        target_page.close()
    except Exception:
        pass

    remaining = get_open_pages()
    if not remaining:
        try:
            state["context"].close()
        except Exception:
            pass
        if state["browser"] is not None:
            try:
                state["browser"].close()
            except Exception:
                pass
        state["context"] = None
        state["browser"] = None
        state["current_profile"] = None
        state["active_tab_index"] = 0
        _clean_orphaned_playwright_processes()
        return {
            "status": "browser_closed",
            "message": f"Closed '{closed_title}'. That was the last tab, so the browser window closed.",
            "remaining_tabs_count": 0,
            "tabs": [],
        }

    state["active_tab_index"] = min(state.get("active_tab_index", 0), len(remaining) - 1)
    try:
        remaining[state["active_tab_index"]].bring_to_front()
    except Exception:
        pass

    tabs = build_tab_list()
    return {
        "status": "tab_closed",
        "closed_tab": {"title": closed_title, "url": closed_url},
        "remaining_tabs_count": len(tabs),
        "active_tab_index": state["active_tab_index"],
        "tabs": tabs,
    }


def cmd_click(payload):
    """Clicks an element on the active page."""
    selector = (payload.get("selector") or "").strip()
    if not selector:
        return {"error": "no selector provided"}
    timeout_ms = int(payload.get("timeout_ms", 8000))
    double_click = bool(payload.get("double_click", False))
    tab_index = payload.get("tab_index")

    page = get_active_page(tab_index=tab_index)
    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed(timeout=timeout_ms)
    if double_click:
        loc.dblclick(timeout=timeout_ms)
    else:
        loc.click(timeout=timeout_ms)
    page.wait_for_timeout(300)

    return {
        "status": "clicked",
        "selector": selector,
        "url": page.url,
        "title": page.title(),
        "active_tab_index": state["active_tab_index"],
    }


def cmd_type(payload):
    """Types text into an input or textarea."""
    selector = (payload.get("selector") or "").strip()
    if not selector:
        return {"error": "no selector provided"}
    text = str(payload.get("text", ""))
    press_enter = bool(payload.get("press_enter", False))
    clear = bool(payload.get("clear", True))
    timeout_ms = int(payload.get("timeout_ms", 8000))
    tab_index = payload.get("tab_index")

    page = get_active_page(tab_index=tab_index)
    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed(timeout=timeout_ms)
    if clear:
        loc.fill(text, timeout=timeout_ms)
    else:
        loc.type(text, timeout=timeout_ms)
    if press_enter:
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)

    return {
        "status": "typed",
        "selector": selector,
        "text": text,
        "url": page.url,
        "title": page.title(),
        "active_tab_index": state["active_tab_index"],
    }


def cmd_press_key(payload):
    """Presses a keyboard key on the active page."""
    key = (payload.get("key") or "Enter").strip()
    selector = (payload.get("selector") or "").strip()
    tab_index = payload.get("tab_index")

    page = get_active_page(tab_index=tab_index)
    if selector:
        page.locator(selector).first.focus()
    page.keyboard.press(key)
    page.wait_for_timeout(300)

    return {
        "status": "pressed",
        "key": key,
        "url": page.url,
        "title": page.title(),
        "active_tab_index": state["active_tab_index"],
    }


def cmd_scroll(payload):
    """Scrolls the page or scrolls a selector into view."""
    direction = (payload.get("direction") or "down").lower()
    amount = int(payload.get("amount", 500))
    selector = (payload.get("selector") or "").strip()
    tab_index = payload.get("tab_index")

    page = get_active_page(tab_index=tab_index)
    if selector:
        page.locator(selector).first.scroll_into_view_if_needed()
    elif direction == "down":
        page.evaluate(f"window.scrollBy(0, {amount})")
    elif direction == "up":
        page.evaluate(f"window.scrollBy(0, -{amount})")
    elif direction == "top":
        page.evaluate("window.scrollTo(0, 0)")
    elif direction == "bottom":
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    return {
        "status": "scrolled",
        "direction": direction,
        "url": page.url,
        "title": page.title(),
        "active_tab_index": state["active_tab_index"],
    }


def cmd_get_content(payload):
    """Extracts text, interactive DOM elements, or HTML from the page."""
    mode = payload.get("mode", "text")
    max_chars = int(payload.get("max_chars", 4000))
    tab_index = payload.get("tab_index")

    page = get_active_page(tab_index=tab_index)

    if mode == "elements":
        elements = page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const list = document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="link"], h1, h2, h3');
            for (const el of list) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0 || window.getComputedStyle(el).visibility === 'hidden') continue;
                const tag = el.tagName.toLowerCase();
                const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || '').trim().replace(/\\s+/g, ' ');
                const id = el.id ? '#' + el.id : '';
                const name = el.name ? `[name="${el.name}"]` : '';
                const placeholder = el.placeholder || '';
                const key = `${tag}:${id}:${name}:${text.slice(0, 40)}`;
                if (seen.has(key)) continue;
                seen.add(key);
                results.push({
                    tag,
                    id: el.id || undefined,
                    name: el.name || undefined,
                    text: text ? text.slice(0, 80) : undefined,
                    placeholder: placeholder || undefined,
                    type: el.type || undefined,
                    selector_hint: id || name || (text ? `${tag}:has-text("${text.slice(0, 25).replace(/"/g, '')}")` : tag)
                });
                if (results.length >= 60) break;
            }
            return results;
        }""")
        return {
            "url": page.url,
            "title": page.title(),
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "elements": elements,
        }
    elif mode == "html":
        html = page.content()
        return {
            "url": page.url,
            "title": page.title(),
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "content": html[:max_chars],
        }
    else:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        clean_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return {
            "url": page.url,
            "title": page.title(),
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "content": clean_text[:max_chars],
        }


def cmd_screenshot(payload):
    """Captures a screenshot of the visible screen or full page."""
    full_page = bool(payload.get("full_page", False))
    save_path = payload.get("save_path")
    tab_index = payload.get("tab_index")

    page = get_active_page(tab_index=tab_index)

    if not save_path:
        captures_dir = os.path.join(ROOT, "notes", "captures")
        os.makedirs(captures_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(captures_dir, f"browser_{ts}.png")
    else:
        save_path = os.path.expanduser(save_path)
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

    page.screenshot(path=save_path, full_page=full_page)
    filename = os.path.basename(save_path)
    return {
        "status": "screenshot_saved",
        "path": save_path,
        "filename": filename,
        "screenshot_url": f"/captures/{filename}",
        "url": page.url,
        "title": page.title(),
        "active_tab_index": state["active_tab_index"],
    }


COMMANDS = {
    "/open": cmd_open,
    "/close": cmd_close,
    "/list": cmd_list,
    "/switch_tab": cmd_switch_tab,
    "/list_profiles": cmd_list_profiles,
    "/click": cmd_click,
    "/type": cmd_type,
    "/press_key": cmd_press_key,
    "/scroll": cmd_scroll,
    "/content": cmd_get_content,
    "/screenshot": cmd_screenshot,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep quiet; JARVIS logs the interesting parts

    def _reply(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply({"ok": True})
        else:
            self._reply({"error": "unknown endpoint"}, 404)

    def do_POST(self):
        cmd = COMMANDS.get(self.path)
        if cmd is None:
            self._reply({"error": "unknown endpoint"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            payload = {}

        try:
            result = cmd(payload)
            self._reply(result)
        except PlaywrightTimeoutError as e:
            # Selector or navigation timeout: browser context is still healthy and alive!
            print(f"[browser-daemon] command {self.path} timed out: {e}", file=sys.stderr)
            self._reply({"error": f"Action timed out: {e}"}, 408)
        except PlaywrightError as e:
            err_str = str(e)
            print(f"[browser-daemon] command {self.path} Playwright error: {err_str}", file=sys.stderr)
            # Only reset browser if target window/page was actually destroyed or disconnected
            if "closed" in err_str.lower() or "session closed" in err_str.lower():
                state["context"] = None
                state["browser"] = None
                state["pw"] = None
                _clean_orphaned_playwright_processes()
                self._reply({"error": f"Browser was closed: {err_str}"}, 500)
            else:
                self._reply({"error": f"Browser action failed: {err_str}"}, 500)
        except Exception as e:
            print(f"[browser-daemon] command {self.path} unexpected error: {e}", file=sys.stderr)
            self._reply({"error": f"Internal browser daemon error: {e}"}, 500)


if __name__ == "__main__":
    print(f"[browser-daemon] listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

