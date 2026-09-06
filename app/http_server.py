"""app/http_server.py — HTTP routing (Controller adapter) + static file
serving for viewer/ (View delivery).

Business logic lives in app/controllers.py; this module only translates
real HTTP requests/responses into calls against those plain functions, and
serves the viewer/ frontend as static files (nothing outside it is
reachable — path-traversal guard unchanged from the original server.py).
"""
import json
import os
import ssl
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import controllers, telemetry, tls
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
    # Everything downstream on this thread — provider calls, the tool loop —
    # can now report progress without threading an id through every layer.
    telemetry.bind_job(job_id)
    telemetry.activity("Starting…")
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


# The viewer's two pollers: /status every 2s for the system panel, and
# /chat/result every 1.5s for as long as an answer is pending. Logged like
# every other request they emit ~70 lines a minute of "200 -", which is exactly
# how the two lines that mattered — five dead TTS keys, a rate-limited model —
# ended up buried in a wall of successful noise during a two-minute wait.
_QUIET_POLL_PATHS = ("/status", "/chat/result")


class JarvisHandler(BaseHTTPRequestHandler):
    server_version = "JarvisServer/1.0"

    def log_message(self, fmt, *args):
        # A poll that SUCCEEDS is not news, so it is silent. A poll that fails
        # is news, and still gets logged — as does everything else.
        status = str(args[1]) if len(args) > 1 else ""
        if status.startswith("2"):
            path = getattr(self, "path", "").split("?")[0]
            if path in _QUIET_POLL_PATHS:
                return
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
            try:
                self._send_json(controllers.handle_status(Config.load()))
            except Exception as e:
                self.log_message("handle_status failed: %s", e)
                self._send_json({
                    "busy": False,
                    "stuck": False,
                    "running": None,
                    "error_count": 1,
                    "events": [],
                    "providers": [],
                    "provider_in_use": None,
                    "model": None,
                    "tools": 0,
                    "sessions": [],
                    "problems": [f"status check failed: {e}"],
                })
            return

        if url_path == "/chat/result":
            self._handle_chat_result(parsed.query)
            return

        if url_path == "/spotify/state":
            # Feeds the viewer's now-playing panel. Read-only, and it reports
            # its own failures rather than 500-ing, because "Spotify is not
            # reachable" is exactly the thing the panel exists to show.
            from .connectors.spotify import spotify_playback_snapshot
            try:
                self._send_json(spotify_playback_snapshot(Config.load()))
            except Exception as e:
                self._send_json({"status": "error", "error": str(e)})
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
        elif self.path == "/spotify/control":
            self._handle_spotify_control()
        else:
            self.send_error(404, "Not found")

    def _handle_spotify_control(self):
        """Transport buttons on the now-playing panel.

        Deliberately a thin pass-through to the same tool functions the model
        calls, so the panel and JARVIS drive playback through one code path
        rather than two that can disagree.
        """
        from .connectors.spotify import execute_spotify_tool
        body = self._read_json_body()
        cfg = Config.load()
        action = body.get("action", "")
        if action == "volume":
            result = execute_spotify_tool(cfg, "spotify_set_volume",
                                          {"volume_percent": body.get("volume_percent")})
        elif action in ("show_panel", "hide_panel"):
            # The panel's own ✕ and any "open the player" request land here, so
            # the viewer and the model raise/lower the same flag.
            result = execute_spotify_tool(cfg, f"spotify_{action}", {})
        elif action in ("play", "resume", "pause", "next", "previous", "stop", "status"):
            # The connector's vocabulary calls it "resume"; the panel's button
            # says "play". Translate here rather than teaching the UI jargon.
            verb = "resume" if action == "play" else action
            result = execute_spotify_tool(cfg, "spotify_playback_control", {"action": verb})
        else:
            result = {"status": "error", "error": f"Unsupported control action: {action!r}"}
        self._send_json(result)

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
            # The poll is already happening every 1.5s, so live progress rides
            # back on it for free — no extra requests, and the turn stops being
            # a silent void the user cannot tell from a hang.
            self._send_json({"status": "pending", **(telemetry.job_activity(job_id) or {})})
        else:
            self._send_json({"status": "done", "http_status": snapshot["http_status"],
                             "result": snapshot["result"]})

    def _handle_remember(self):
        body = self._read_json_body()
        cfg = Config.load()
        result, status = controllers.handle_remember(cfg, NOTES_DIR, VIEWER_DIR, body)
        self._send_json(result, status=status)


class TLSThreadingHTTPServer(ThreadingHTTPServer):
    """The same server, speaking TLS.

    The handshake is deliberately deferred out of the accept loop and into
    the per-connection worker thread: a client that opens a socket and then
    stalls — or that speaks plain http:// at the https port, which is the
    single most common way to arrive here by accident — would otherwise
    block every other connection until it timed out.
    """

    def __init__(self, address, handler, ssl_context):
        self.ssl_context = ssl_context
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.ssl_context.wrap_socket(sock, server_side=True,
                                            do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address):
        request.settimeout(20)
        request.do_handshake()
        request.settimeout(None)
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        # A failed handshake is routine here (a port scan, a browser that
        # declined the self-signed certificate, http:// at the wrong port);
        # it is worth one line, not a traceback claiming the server broke.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            sys.stderr.write(f"[jarvis] tls: dropped {client_address[0]} — {exc}\n")
            return
        super().handle_error(request, client_address)


def _start_https(bind, port):
    """Builds and binds the HTTPS listener, or returns None with a reason
    logged — https is an addition to the http listener, never a precondition
    for it, so nothing here may stop the server from coming up."""
    try:
        cert_path, key_path = tls.ensure_certificate()
        context = tls.make_ssl_context(cert_path, key_path)
        return TLSThreadingHTTPServer((bind, port), JarvisHandler, context)
    except OSError as e:
        if getattr(e, "errno", None) == 98:
            print(f"[jarvis] https disabled on port {port} — port {port} is already in use", file=sys.stderr)
            return None
        print(f"[jarvis] https disabled on port {port} — {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[jarvis] https disabled on port {port} — {e}", file=sys.stderr)
        return None


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
    try:
        server = ThreadingHTTPServer((bind, port), JarvisHandler)
    except OSError as e:
        if getattr(e, "errno", None) == 98:
            print(f"\n[jarvis] ERROR: Port {port} is already in use by another process.", file=sys.stderr)
            print(f"[jarvis] Another instance of JARVIS or another service is listening on port {port}.", file=sys.stderr)
            print(f"[jarvis] You can stop it with: fuser -k {port}/tcp", file=sys.stderr)
            sys.exit(1)
        raise

    display_host = "localhost" if bind in ("0.0.0.0", "::") else bind
    print(f"[jarvis] Serving on http://{display_host}:{port}  (open this in Chrome)")

    # The camera and the wake-word microphone are secure-context features, so
    # over the LAN they only work on https — hence a second listener on the
    # same handler rather than a replacement for the http one.
    https_server = None
    if cfg.get("server.https_enabled", True):
        # 4443, not port + 1: 4701 belongs to the browser-automation daemon
        # (tools/browser_daemon.py) and taking it would break every browser tool.
        https_port = cfg.get("server.https_port", 4443)
        https_server = _start_https(bind, https_port)
        if https_server is not None:
            print(f"[jarvis] Serving on https://{display_host}:{https_port}  "
                  f"(self-signed — accept the browser warning once)")
            threading.Thread(target=https_server.serve_forever, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[jarvis] Shutting down.")
        server.shutdown()
        if https_server is not None:
            https_server.shutdown()


if __name__ == "__main__":
    main()
