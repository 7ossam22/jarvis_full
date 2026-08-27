"""app/http_server.py — HTTP routing (Controller adapter) + static file
serving for viewer/ (View delivery).

Business logic lives in app/controllers.py; this module only translates
real HTTP requests/responses into calls against those plain functions, and
serves the viewer/ frontend as static files (nothing outside it is
reachable — path-traversal guard unchanged from the original server.py).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import controllers
from .config import Config
from .graph import regenerate_graph

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER_DIR = os.path.join(ROOT, "viewer")
NOTES_DIR = os.path.join(ROOT, "notes")
CONFIG_PATH = os.path.join(ROOT, "config.json")


class JarvisHandler(BaseHTTPRequestHandler):
    server_version = "JarvisServer/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[jarvis] " + (fmt % args) + "\n")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- static file serving, viewer/ only, plus GET /config ---------------

    def do_GET(self):
        url_path = self.path.split("?", 1)[0]

        if url_path == "/config":
            self._send_json(controllers.get_public_config(Config.load()))
            return

        if url_path.startswith("/captures/"):
            rel_file = url_path[len("/captures/"):].lstrip("/")
            captures_dir = os.path.join(NOTES_DIR, "captures")
            target = os.path.normpath(os.path.join(captures_dir, rel_file))
            if not (target == captures_dir or target.startswith(captures_dir + os.sep)):
                self.send_error(403, "Forbidden")
                return
            if not os.path.isfile(target):
                self.send_error(404, "Not found")
                return
            content_type = self._guess_content_type(target)
            with open(target, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if url_path == "/":
            url_path = "/index.html"

        # Normalize and forbid escaping viewer/ (blocks .., absolute paths, etc.)
        rel_path = url_path.lstrip("/")
        target = os.path.normpath(os.path.join(VIEWER_DIR, rel_path))
        if not (target == VIEWER_DIR or target.startswith(VIEWER_DIR + os.sep)):
            self.send_error(403, "Forbidden")
            return
        if not os.path.isfile(target):
            self.send_error(404, "Not found")
            return

        content_type = self._guess_content_type(target)
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _guess_content_type(path):
        ext = os.path.splitext(path)[1].lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")

    # ---- API -----------------------------------------------------------

    def do_POST(self):
        if self.path == "/chat":
            self._handle_chat()
        elif self.path == "/remember":
            self._handle_remember()
        elif self.path == "/speak":
            self._handle_speak()
        elif self.path == "/jira/action":
            self._handle_jira_action()
        else:
            self.send_error(404, "Not found")

    def _handle_jira_action(self):
        from .connectors.jira import execute_jira_tool
        body = self._read_json_body()
        cfg = Config.load()
        action = body.get("action", "")
        payload = body.get("payload", {})
        result = execute_jira_tool(cfg, action, payload)
        self._send_json(result)


    def _handle_speak(self):
        # Proxies ElevenLabs TTS so the API key never reaches the browser (and
        # never reaches anyone else on the LAN this server is bound to).
        body = self._read_json_body()
        cfg = Config.load()
        kind, payload, status = controllers.handle_speak(cfg, body)
        if kind == "json":
            self._send_json(payload, status=status)
            return
        audio, content_type = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def _handle_chat(self):
        body = self._read_json_body()
        cfg = Config.load()
        result, status = controllers.handle_chat(cfg, NOTES_DIR, VIEWER_DIR, body)
        self._send_json(result, status=status)

    def _handle_remember(self):
        body = self._read_json_body()
        cfg = Config.load()
        result, status = controllers.handle_remember(cfg, NOTES_DIR, VIEWER_DIR, body)
        self._send_json(result, status=status)


def main():
    cfg = Config.load()

    if not os.path.exists(CONFIG_PATH):
        print("[jarvis] No config.json found — copy config.example.json to config.json "
              "and paste your Anthropic API key in, or install the `claude` CLI for the "
              "free fallback mode.", file=sys.stderr)

    print("[jarvis] Building graph from notes/ …")
    graph = regenerate_graph(NOTES_DIR, VIEWER_DIR)
    print(f"[jarvis] {len(graph['nodes'])} notes, {len(graph['links'])} links indexed.")

    port = cfg.get("server.port", 4700)
    bind = cfg.get("server.bind", "0.0.0.0")
    server = ThreadingHTTPServer((bind, port), JarvisHandler)
    print(f"[jarvis] Serving on http://{bind}:{port}  (open this in Chrome)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[jarvis] Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
