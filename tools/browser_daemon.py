#!/usr/bin/env python3
"""tools/browser_daemon.py — JARVIS's hands on a real browser (Playwright).

Runs OUTSIDE the stdlib-only JARVIS server because it needs the playwright
pip package — launch it with .venv-browser/bin/python (see
tools/setup_browser.sh). The JARVIS server's browser connector
(app/connectors/browser.py) starts this on demand and talks to it over
http://127.0.0.1:4701.

Single-threaded HTTPServer on purpose: Playwright's sync API objects must
stay on the thread that created them, so every command runs sequentially on
the main thread.

Endpoints (JSON in/out):
  GET  /health          → {"ok": true}
  POST /open   {"url", "new_tab"}  → opens a new tab in a visible maximized window
  POST /close  {"scope": "tab" | "all"}  → close latest tab, or the browser
  POST /list             → titles/urls of open tabs
  POST /click  {"selector", "double_click"} → clicks an element
  POST /type   {"selector", "text", "press_enter", "clear"} → types into input/textarea
  POST /press_key {"key", "selector"} → presses a keyboard key
  POST /scroll {"direction", "amount", "selector"} → scrolls page or element
  POST /content {"mode", "max_chars"} → extracts text, interactive elements, or html
  POST /screenshot {"full_page", "save_path"} → captures a screenshot to disk
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright, Error as PlaywrightError

PORT = 4701
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

state = {"pw": None, "browser": None, "context": None}


def ensure_browser():
    browser = state["browser"]
    if browser is not None and browser.is_connected() and state["context"].pages is not None:
        return
    if state["pw"] is None:
        state["pw"] = sync_playwright().start()
    state["browser"] = state["pw"].chromium.launch(
        headless=False, args=["--start-maximized"]
    )
    state["context"] = state["browser"].new_context(no_viewport=True)


def get_active_page():
    ensure_browser()
    pages = state["context"].pages
    if not pages:
        return state["context"].new_page()
    return pages[-1]


def cmd_open(payload):
    url = (payload.get("url") or "").strip()
    if not url:
        return {"error": "no url given"}
    if not url.startswith(("http://", "https://", "file://", "about:")):
        url = "https://" + url
    ensure_browser()
    pages = state["context"].pages
    if pages and not payload.get("new_tab"):
        page = pages[-1]
    else:
        page = state["context"].new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.bring_to_front()
    return {"status": "opened", "url": page.url, "title": page.title()}


def cmd_close(payload):
    scope = payload.get("scope") or "tab"
    browser = state["browser"]
    if browser is None or not browser.is_connected():
        return {"status": "nothing open"}
    if scope == "all":
        browser.close()
        state["browser"] = state["context"] = None
        return {"status": "browser closed"}
    pages = state["context"].pages
    if not pages:
        return {"status": "nothing open"}
    pages[-1].close()
    if not state["context"].pages:
        browser.close()
        state["browser"] = state["context"] = None
    return {"status": "tab closed"}


def cmd_list(_payload):
    browser = state["browser"]
    if browser is None or not browser.is_connected():
        return {"tabs": []}
    return {"tabs": [{"title": p.title(), "url": p.url} for p in state["context"].pages]}


def cmd_click(payload):
    selector = (payload.get("selector") or "").strip()
    if not selector:
        return {"error": "no selector provided"}
    timeout_ms = int(payload.get("timeout_ms", 8000))
    double_click = bool(payload.get("double_click", False))
    page = get_active_page()

    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed(timeout=timeout_ms)
    if double_click:
        loc.dblclick(timeout=timeout_ms)
    else:
        loc.click(timeout=timeout_ms)
    page.wait_for_timeout(300)
    return {"status": "clicked", "selector": selector, "url": page.url, "title": page.title()}


def cmd_type(payload):
    selector = (payload.get("selector") or "").strip()
    if not selector:
        return {"error": "no selector provided"}
    text = str(payload.get("text", ""))
    press_enter = bool(payload.get("press_enter", False))
    clear = bool(payload.get("clear", True))
    timeout_ms = int(payload.get("timeout_ms", 8000))
    page = get_active_page()

    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed(timeout=timeout_ms)
    if clear:
        loc.fill(text, timeout=timeout_ms)
    else:
        loc.type(text, timeout=timeout_ms)
    if press_enter:
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
    return {"status": "typed", "selector": selector, "text": text, "url": page.url}


def cmd_press_key(payload):
    key = (payload.get("key") or "Enter").strip()
    selector = (payload.get("selector") or "").strip()
    page = get_active_page()
    if selector:
        page.locator(selector).first.focus()
    page.keyboard.press(key)
    page.wait_for_timeout(300)
    return {"status": "pressed", "key": key, "url": page.url}


def cmd_scroll(payload):
    direction = (payload.get("direction") or "down").lower()
    amount = int(payload.get("amount", 500))
    selector = (payload.get("selector") or "").strip()
    page = get_active_page()

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
    return {"status": "scrolled", "direction": direction, "url": page.url}


def cmd_get_content(payload):
    mode = payload.get("mode", "text")
    max_chars = int(payload.get("max_chars", 4000))
    page = get_active_page()

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
        return {"url": page.url, "title": page.title(), "elements": elements}
    elif mode == "html":
        html = page.content()
        return {"url": page.url, "title": page.title(), "content": html[:max_chars]}
    else:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        clean_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return {"url": page.url, "title": page.title(), "content": clean_text[:max_chars]}


def cmd_screenshot(payload):
    full_page = bool(payload.get("full_page", False))
    save_path = payload.get("save_path")
    page = get_active_page()

    if not save_path:
        captures_dir = os.path.join(ROOT, "notes", "captures")
        os.makedirs(captures_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(captures_dir, f"browser_{ts}.png")

    page.screenshot(path=save_path, full_page=full_page)
    return {"status": "screenshot_saved", "path": save_path, "url": page.url, "title": page.title()}


COMMANDS = {
    "/open": cmd_open,
    "/close": cmd_close,
    "/list": cmd_list,
    "/click": cmd_click,
    "/type": cmd_type,
    "/press_key": cmd_press_key,
    "/scroll": cmd_scroll,
    "/content": cmd_get_content,
    "/screenshot": cmd_screenshot,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet; the JARVIS server logs the interesting parts

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
            self._reply(cmd(payload))
        except PlaywrightError as e:
            # Browser window manually closed mid-command, page crashed, bad
            # URL … reset so the next command relaunches cleanly.
            state["browser"] = state["context"] = None
            self._reply({"error": f"browser action failed: {e}"}, 500)


if __name__ == "__main__":
    print(f"[browser-daemon] listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

