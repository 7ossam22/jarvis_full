#!/usr/bin/env python3
"""
server.py — JARVIS backend.

- Serves the viewer/ folder as static files (nothing outside it is reachable).
- POST /chat      — the Brain: keyword-retrieval over notes/ + Anthropic Messages API
                     (falls back to `claude -p`, then a canned response, so the demo
                     still works before a key is configured).
- POST /remember   — Total Recall: writes a new note to notes/captures/, regenerates
                      the graph, and hands the client enough to animate a new star.

Standard library only. No pip installs.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import build as graph_builder  # noqa: E402  (build.py — reused for regeneration + scoring)

VIEWER_DIR = os.path.join(ROOT, "viewer")
NOTES_DIR = os.path.join(ROOT, "notes")
CAPTURES_DIR = os.path.join(NOTES_DIR, "captures")
CONFIG_PATH = os.path.join(ROOT, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(ROOT, "config.example.json")
PORT = 4700

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "up", "about", "into", "over", "after", "what", "when",
    "where", "who", "why", "how", "do", "does", "did", "can", "could",
    "should", "would", "will", "shall", "my", "your", "our", "their", "his",
    "her", "its", "i", "you", "we", "they", "it", "this", "that", "these",
    "those", "me", "us", "them", "notes", "note", "tell", "please", "sir",
}

WORD_RE = re.compile(r"[a-z0-9']+")

SYSTEM_PROMPT = """You are JARVIS: a dry, impeccably polite British butler with a razor wit,
serving as the user's personal knowledge assistant. Address the user as "sir" occasionally —
not every sentence, that gets tedious. One genuinely funny line beats three bland ones.

Rules:
- When the user asks something answerable from the SOURCE NOTES below, answer using ONLY
  those notes, in one witty sentence plus the key facts — never just recite the note back,
  it is already on their screen. If the notes don't cover it, say so plainly (with a touch
  of wit), don't invent facts.
- When the question needs information the notes don't have and the notes wouldn't plausibly
  cover it (current events, prices, weather, "what is X", anything time-sensitive or about
  the outside world), use your web search tool rather than guessing or refusing. Search
  first, then answer from what you found — briefly cite what kind of source it came from
  ("according to their site", "recent reports say") without reading out full URLs.
- When there are no relevant SOURCE NOTES for this turn (small talk, jokes, general chat),
  just be yourself — charming, brief, helpful. Do not pretend to consult notes that aren't
  there.
- Keep answers short: 2-3 sentences, spoken-friendly (this gets read aloud by text-to-speech).
"""

NO_BRAIN_APOLOGY = (
    "I'm terribly sorry, sir — I appear to be without a working brain at the moment. "
    "No Anthropic API key is configured in config.json, and I couldn't find the `claude` "
    "CLI on this machine either."
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else None
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def has_real_api_key(cfg):
    key = cfg.get("api_key", "")
    return bool(key) and "PUT-YOUR-KEY-HERE" not in key and key.strip() != ""


def has_elevenlabs_key(cfg):
    key = cfg.get("elevenlabs_api_key", "")
    return bool(key) and "PUT-YOUR" not in key and key.strip() != ""


# ---------------------------------------------------------------------------
# Retrieval — keyword overlap, title matches weigh extra
# ---------------------------------------------------------------------------

def tokenize(text):
    return {w for w in WORD_RE.findall(text.lower()) if w not in STOPWORDS and len(w) > 1}


def score_notes(question, nodes):
    q_words = tokenize(question)
    if not q_words:
        return []
    scored = []
    for node in nodes:
        title_words = tokenize(node["label"])
        body_words = tokenize(node.get("excerpt", ""))
        score = 5 * len(q_words & title_words) + 1 * len(q_words & body_words)
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def top_notes(question, nodes, limit=6):
    return [node for _score, node in score_notes(question, nodes)[:limit]]


def most_related_note(text, nodes, exclude_id=None):
    candidates = [n for n in nodes if n["id"] != exclude_id]
    scored = score_notes(text, candidates)
    return scored[0][1] if scored else None


# ---------------------------------------------------------------------------
# Model calling — Anthropic API, then `claude -p` CLI, then a canned fallback
# ---------------------------------------------------------------------------

def call_anthropic(cfg, system_prompt, messages):
    api_key = cfg["api_key"]
    model = cfg.get("model") or "claude-sonnet-5"
    payload = json.dumps({
        "model": model,
        "max_tokens": 400,
        "system": system_prompt,
        "messages": messages,
        # Server-side web search — Claude decides when to use it and the results
        # come back merged into the response, no extra round trip needed here.
        "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return "".join(block.get("text", "") for block in body.get("content", []))


def call_claude_cli(system_prompt, messages):
    # Fallback: shell out to the user's Claude Code subscription.
    # --allowedTools WebSearch,WebFetch lets JARVIS look things up (and read the page,
    # not just snippets) even without an API key — non-interactive mode can't prompt
    # for tool permission, so both must be granted upfront.
    convo = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    full_prompt = f"{system_prompt}\n\n{convo}\n\nASSISTANT:"
    result = subprocess.run(
        ["claude", "-p", full_prompt, "--allowedTools", "WebSearch,WebFetch"],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr or "claude CLI returned no output")
    return result.stdout.strip()


DEFAULT_ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — warm British male


def call_elevenlabs_tts(cfg, text):
    """Returns (audio_bytes, content_type). Raises on any failure — caller falls
    back to the browser's own speechSynthesis, so this is never a hard dependency."""
    api_key = cfg["elevenlabs_api_key"]
    voice_id = cfg.get("elevenlabs_voice_id") or DEFAULT_ELEVENLABS_VOICE_ID
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "xi-api-key": api_key,
            "accept": "audio/mpeg",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(), "audio/mpeg"


def call_model(cfg, system_prompt, messages, fallback_text):
    if has_real_api_key(cfg):
        try:
            return call_anthropic(cfg, system_prompt, messages)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            print(f"[jarvis] Anthropic API call failed ({e}); trying claude CLI…", file=sys.stderr)
    try:
        return call_claude_cli(system_prompt, messages)
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, OSError) as e:
        print(f"[jarvis] claude CLI fallback failed ({e})", file=sys.stderr)
    return fallback_text


# ---------------------------------------------------------------------------
# Session history (in-memory, per server process)
# ---------------------------------------------------------------------------

SESSIONS = {}
MAX_HISTORY_TURNS = 6


def get_history(session_id):
    return SESSIONS.setdefault(session_id, [])


def append_history(session_id, role, content):
    hist = get_history(session_id)
    hist.append({"role": role, "content": content})
    del hist[: max(0, len(hist) - MAX_HISTORY_TURNS * 2)]


# ---------------------------------------------------------------------------
# Graph regeneration
# ---------------------------------------------------------------------------

def regenerate_graph():
    graph = graph_builder.build_graph([NOTES_DIR])
    out_path = os.path.join(VIEWER_DIR, "graph-data.js")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by build.py / server.py — do not edit by hand.\n")
        f.write("const GRAPH = ")
        f.write(json.dumps(graph, indent=2, ensure_ascii=False))
        f.write(";\n")
    return graph


def slugify_title(text, max_words=7):
    words = re.findall(r"[A-Za-z0-9']+", text)[:max_words]
    title = " ".join(w.capitalize() if w.islower() else w for w in words) or "Untitled Note"
    return title


def safe_filename(title):
    cleaned = re.sub(r"[^A-Za-z0-9 \-']", "", title).strip()
    return (cleaned or "Untitled Note") + ".md"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

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

    # ---- static file serving, viewer/ only -------------------------------

    def do_GET(self):
        url_path = self.path.split("?", 1)[0]
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

    # ---- API ---------------------------------------------------------------

    def do_POST(self):
        if self.path == "/chat":
            self._handle_chat()
        elif self.path == "/remember":
            self._handle_remember()
        elif self.path == "/speak":
            self._handle_speak()
        else:
            self.send_error(404, "Not found")

    def _handle_speak(self):
        # Proxies ElevenLabs TTS so the API key never reaches the browser (and
        # never reaches anyone else on the LAN this server is bound to).
        body = self._read_json_body()
        text = (body.get("text") or "").strip()
        if not text:
            self._send_json({"error": "empty text"}, status=400)
            return

        cfg = load_config()
        if not has_elevenlabs_key(cfg):
            self._send_json({"error": "no elevenlabs key configured"}, status=404)
            return

        try:
            audio, content_type = call_elevenlabs_tts(cfg, text)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            print(f"[jarvis] ElevenLabs TTS failed ({e})", file=sys.stderr)
            self._send_json({"error": "tts failed"}, status=502)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def _handle_chat(self):
        body = self._read_json_body()
        question = (body.get("message") or "").strip()
        session_id = body.get("session_id") or str(uuid.uuid4())
        if not question:
            self._send_json({"error": "empty message"}, status=400)
            return

        cfg = load_config()
        graph = regenerate_graph()
        nodes = graph["nodes"]
        relevant = top_notes(question, nodes, limit=6)

        if relevant:
            context = "\n\n".join(
                f"### {n['label']} (group: {n['group']})\n{n['excerpt']}" for n in relevant
            )
            user_content = (
                f"SOURCE NOTES:\n{context}\n\n"
                f"USER QUESTION: {question}"
            )
        else:
            user_content = (
                "No SOURCE NOTES were relevant to this message — treat it as small talk / "
                f"general conversation, not a notes lookup.\n\nUSER MESSAGE: {question}"
            )

        history = get_history(session_id)
        messages = history + [{"role": "user", "content": user_content}]

        fallback = NO_BRAIN_APOLOGY
        if relevant:
            fallback += f" By keyword match alone, the closest note is '{relevant[0]['label']}'."

        answer = call_model(cfg, SYSTEM_PROMPT, messages, fallback)

        append_history(session_id, "user", user_content)
        append_history(session_id, "assistant", answer)

        self._send_json({
            "answer": answer,
            "nodes": [n["id"] for n in relevant],
            "session_id": session_id,
        })

    def _handle_remember(self):
        body = self._read_json_body()
        raw_text = (body.get("text") or "").strip()
        session_id = body.get("session_id") or str(uuid.uuid4())
        if not raw_text:
            self._send_json({"error": "empty text"}, status=400)
            return

        content_text = re.sub(r"^\s*remember that\s*", "", raw_text, flags=re.IGNORECASE).strip()
        content_text = content_text or raw_text

        # Find the closest existing note BEFORE writing the new one, so it can't match itself.
        graph_before = graph_builder.build_graph([NOTES_DIR])
        related = most_related_note(content_text, graph_before["nodes"])

        title = slugify_title(content_text)
        os.makedirs(CAPTURES_DIR, exist_ok=True)
        filename = safe_filename(title)
        filepath = os.path.join(CAPTURES_DIR, filename)
        # avoid clobbering an existing capture with the same title
        n = 2
        base_filepath = filepath
        while os.path.exists(filepath):
            filepath = base_filepath[:-3] + f" {n}.md"
            n += 1

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{content_text}\n")

        graph_after = regenerate_graph()
        new_node = next(
            (n for n in graph_after["nodes"] if os.path.realpath(n["path"]) == os.path.realpath(filepath)),
            None,
        )

        cfg = load_config()
        confirmation_fallback = f"Noted, sir — filed under '{title}'."
        messages = [{
            "role": "user",
            "content": (
                "In ONE short witty British-butler line, confirm you've just filed this new "
                f"note titled '{title}'. Do not repeat the whole note back, just the confirmation."
            ),
        }]
        confirmation = call_model(cfg, SYSTEM_PROMPT, messages, confirmation_fallback)

        self._send_json({
            "node": new_node,
            "related_id": related["id"] if related else None,
            "confirmation": confirmation,
            "notes_count": len(graph_after["nodes"]),
            "session_id": session_id,
        })


def main():
    if not os.path.exists(CONFIG_PATH):
        print("[jarvis] No config.json found — copy config.example.json to config.json "
              "and paste your Anthropic API key in, or install the `claude` CLI for the "
              "free fallback mode.", file=sys.stderr)

    print("[jarvis] Building graph from notes/ …")
    graph = regenerate_graph()
    print(f"[jarvis] {len(graph['nodes'])} notes, {len(graph['links'])} links indexed.")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), JarvisHandler)
    print(f"[jarvis] Serving on http://0.0.0.0:{PORT}  (open this in Chrome, reachable over LAN)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[jarvis] Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
