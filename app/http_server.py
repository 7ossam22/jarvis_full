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
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import controllers, telemetry
from .config import Config
from .graph import regenerate_graph

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER_DIR = os.path.join(ROOT, "viewer")
NOTES_DIR = os.path.join(ROOT, "notes")
CONFIG_PATH = os.path.join(ROOT, "config.json")

# Long tool-using chat runs (e.g. filling a 20+ question form) outlive the
# browser's own request timeout (~5 min in Chrome), so the viewer starts the
# chat as a background job and polls /chat/result for the answer instead of
# holding one request open for the whole run.
_chat_jobs = {}
_chat_jobs_lock = threading.Lock()
_CHAT_JOB_TTL = 15 * 60          # forget finished jobs after 15 min
_CHAT_JOB_MAX_AGE = 60 * 60      # give up on a job that somehow never finishes


def _prune_chat_jobs():
    now = time.time()
    with _chat_jobs_lock:
        for job_id in list(_chat_jobs):
            job = _chat_jobs[job_id]
            finished = job.get("finished")
            if (finished and now - finished > _CHAT_JOB_TTL) or now - job["created"] > _CHAT_JOB_MAX_AGE:
                del _chat_jobs[job_id]


def _run_chat_job(job_id, cfg, body):
    telemetry.job_started(job_id, (body.get("message") or "")[:80] or "chat")
    try:
        result, status = controllers.handle_chat(cfg, NOTES_DIR, VIEWER_DIR, body)
        telemetry.job_finished(job_id, ok=(status == 200))
    except Exception as e:
        sys.stderr.write(f"[jarvis] chat job {job_id} failed: {e}\n")
        telemetry.record("error", "chat job crashed", e)
        telemetry.job_finished(job_id, ok=False, error=e)
        result, status = {"answer": f"I hit an internal error, sir: {e}", "error": str(e)}, 500
    with _chat_jobs_lock:
        job = _chat_jobs.get(job_id)
        if job is not None:
            job.update({"status": "done", "result": result, "http_status": status,
                        "finished": time.time()})


class JarvisHandler(BaseHTTPRequestHandler):
    server_version = "JarvisServer/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[jarvis] " + (fmt % args) + "\n")

    def _send_bytes(self, data, content_type, status=200, no_cache=False):
        # The client may have given up (page refresh, browser request timeout)
        # long before a slow tool-using answer finished — the work is already
        # done at this point, so a vanished socket is not an error worth a
        # thread-killing traceback.
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if no_cache:
                # The viewer is edited live and reloaded in place. Without this
                # the browser heuristically caches js/css (no validator is sent
                # otherwise), so a code change appears not to have taken effect
                # until a manual hard reload — which looks exactly like a bug.
                self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.log_message("client disconnected before the response to %s could be sent", self.path)

    def _send_json(self, payload, status=200):
        self._send_bytes(json.dumps(payload).encode("utf-8"), "application/json", status=status)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- static file serving, viewer/ only, plus GET /config ---------------

    def do_GET(self):
        parsed = urlparse(self.path)
        url_path = parsed.path

        if url_path == "/config":
            self._send_json(controllers.get_public_config(Config.load()))
            return

        if url_path == "/status":
            # Live diagnostics for the viewer's system panel: what is running,
            # whether it looks stuck, and every error that has been recorded
            # rather than merely printed to a terminal nobody is watching.
            self._send_json(controllers.handle_status(Config.load()))
            return

        if url_path == "/chat/result":
            self._handle_chat_result(parsed.query)
            return

        if url_path == "/embeddable":
            # The SHOW window asks whether a page can be iframed before trying,
            # because a cross-origin refusal is invisible to the browser.
            target = (parse_qs(parsed.query).get("url") or [""])[0]
            result, status = controllers.handle_embeddable(Config.load(), {"url": target})
            self._send_json(result, status=status)
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
            self._send_bytes(data, content_type)
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
        self._send_bytes(data, content_type, no_cache=True)

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
        elif self.path == "/assist/form-question":
            # The form autopilot in tools/browser_daemon.py runs in its own
            # process and cannot reach the provider layer, so it asks here when
            # its deterministic rules are exhausted on one question.
            body = self._read_json_body()
            result, status = controllers.handle_form_assist(Config.load(), body)
            self._send_json(result, status=status)
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
        self._send_bytes(audio, content_type, status=status)

    def _handle_chat(self):
        body = self._read_json_body()
        cfg = Config.load()

        # Async mode (used by the viewer): start the run in a worker thread and
        # return a job id right away; the answer is fetched via /chat/result.
        if body.get("async"):
            _prune_chat_jobs()
            job_id = uuid.uuid4().hex
            with _chat_jobs_lock:
                _chat_jobs[job_id] = {"status": "pending", "created": time.time(), "finished": None}
            threading.Thread(target=_run_chat_job, args=(job_id, cfg, body), daemon=True).start()
            self._send_json({"job_id": job_id, "status": "pending"}, status=202)
            return

        # Synchronous mode kept for other clients (curl, scripts, apps).
        try:
            result, status = controllers.handle_chat(cfg, NOTES_DIR, VIEWER_DIR, body)
        except Exception as e:
            sys.stderr.write(f"[jarvis] chat handling failed: {e}\n")
            result, status = {"answer": f"I hit an internal error, sir: {e}", "error": str(e)}, 500
        self._send_json(result, status=status)

    def _handle_chat_result(self, query):
        job_id = (parse_qs(query).get("job_id") or [""])[0]
        with _chat_jobs_lock:
            job = _chat_jobs.get(job_id)
            snapshot = dict(job) if job else None
        if snapshot is None:
            self._send_json({"status": "unknown"}, status=404)
        elif snapshot["status"] == "pending":
            self._send_json({"status": "pending"})
        else:
            self._send_json({"status": "done", "http_status": snapshot["http_status"],
                             "result": snapshot["result"]})

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
