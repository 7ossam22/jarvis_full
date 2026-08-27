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
  POST /open   {"url"}  → opens a new tab in a visible maximized window
  POST /close  {"scope": "tab" | "all"}  → close latest tab, or the browser
  POST /list             → titles/urls of open tabs
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright, Error as PlaywrightError

PORT = 4701

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


def cmd_open(payload):
    url = (payload.get("url") or "").strip()
    if not url:
        return {"error": "no url given"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ensure_browser()
    # Reuse the current tab by default — a string of "open X … open Y"
    # commands navigates one tab like a person would, instead of piling up
    # windows. A fresh tab only on explicit request.
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


COMMANDS = {"/open": cmd_open, "/close": cmd_close, "/list": cmd_list}


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
