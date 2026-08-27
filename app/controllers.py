"""app/controllers.py — request handling logic (Controller layer).

Each function takes plain data in and returns plain data out — no HTTP
specifics (no self.send_response, no headers). app/http_server.py adapts
these to actual HTTP requests/responses. This separation is what makes the
business logic here testable/reusable independent of the transport.
"""
import os
import re
import sys
import urllib.error
import uuid

from . import history, persona, retrieval
from .graph import build_graph, regenerate_graph
from .images import extract_media_references, extract_image_references
from .providers.llm import call_model
from .providers.tts import get_tts_providers


def _slugify_title(text, max_words=7):
    words = re.findall(r"[A-Za-z0-9']+", text)[:max_words]
    return " ".join(w.capitalize() if w.islower() else w for w in words) or "Untitled Note"


def _safe_filename(title):
    cleaned = re.sub(r"[^A-Za-z0-9 \-']", "", title).strip()
    return (cleaned or "Untitled Note") + ".md"


def get_public_config(cfg):
    return cfg.public_dict()


def handle_chat(cfg, notes_dir, viewer_dir, body):
    """Returns (response_dict, http_status)."""
    question = (body.get("message") or "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())
    if not question:
        return {"error": "empty message"}, 400

    graph = regenerate_graph(notes_dir, viewer_dir)
    nodes = graph["nodes"]
    limit = cfg.get("retrieval.top_notes_limit", 6)
    relevant = retrieval.top_notes(question, nodes, limit=limit)

    if relevant:
        context = "\n\n".join(
            f"### {n['label']} (group: {n['group']})\n{n['excerpt']}" for n in relevant
        )
        user_content = f"SOURCE NOTES:\n{context}\n\nUSER QUESTION: {question}"
    else:
        user_content = (
            "No SOURCE NOTES were relevant to this message.\n"
            "If the user is asking to check, fetch, or send emails, or look up information, "
            "use your available tools (such as Gmail tools or WebSearch) to fulfill the request.\n\n"
            f"USER MESSAGE: {question}"
        )


    max_turns = cfg.get("retrieval.max_history_turns", 6)
    hist = history.get_history(session_id)
    messages = hist + [{"role": "user", "content": user_content}]

    fallback = persona.no_brain_apology(cfg)
    if relevant:
        fallback += f" By keyword match alone, the closest note is '{relevant[0]['label']}'."

    system_prompt = persona.build_system_prompt(cfg)
    answer = call_model(cfg, system_prompt, messages, fallback)
    max_images = cfg.get("images.max_gallery", 6)
    answer, image_urls, video_urls, source_urls, show_url = extract_media_references(answer, max_images)

    # The spoken/displayed answer never contains URLs (persona rule — TTS would
    # read them as gibberish), but later turns like "send that to Discord" need
    # them, so stash them in the history copy only as a reference footnote.
    hist_answer = answer
    ref_links = source_urls + video_urls + image_urls
    if show_url and show_url not in ref_links:
        ref_links.insert(0, show_url)
    if ref_links:
        hist_answer += "\n[reference links: " + ", ".join(ref_links[:4]) + "]"

    history.append_history(session_id, "user", user_content, max_turns)
    history.append_history(session_id, "assistant", hist_answer, max_turns)

    return {
        "answer": answer,
        "nodes": [n["id"] for n in relevant],
        "session_id": session_id,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "show_url": show_url,
    }, 200



def handle_remember(cfg, notes_dir, viewer_dir, body):
    """Returns (response_dict, http_status)."""
    raw_text = (body.get("text") or "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())
    if not raw_text:
        return {"error": "empty text"}, 400

    content_text = re.sub(r"^\s*remember that\s*", "", raw_text, flags=re.IGNORECASE).strip()
    content_text = content_text or raw_text

    captures_dir = os.path.join(notes_dir, "captures")
    # Find the closest existing note BEFORE writing the new one, so it can't match itself.
    graph_before = build_graph([notes_dir])
    related = retrieval.most_related_note(content_text, graph_before["nodes"])

    title = _slugify_title(content_text)
    os.makedirs(captures_dir, exist_ok=True)
    filename = _safe_filename(title)
    filepath = os.path.join(captures_dir, filename)
    # avoid clobbering an existing capture with the same title
    n = 2
    base_filepath = filepath
    while os.path.exists(filepath):
        filepath = base_filepath[:-3] + f" {n}.md"
        n += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{content_text}\n")

    graph_after = regenerate_graph(notes_dir, viewer_dir)
    new_node = next(
        (n for n in graph_after["nodes"] if os.path.realpath(n["path"]) == os.path.realpath(filepath)),
        None,
    )

    address_term = cfg.get("persona.address_term", "sir")
    confirmation_fallback = f"Noted, {address_term} — filed under '{title}'."
    messages = [{
        "role": "user",
        "content": (
            "In ONE short witty British-butler line, confirm you've just filed this new "
            f"note titled '{title}'. Do not repeat the whole note back, just the confirmation."
        ),
    }]
    system_prompt = persona.build_system_prompt(cfg)
    confirmation = call_model(cfg, system_prompt, messages, confirmation_fallback)

    return {
        "node": new_node,
        "related_id": related["id"] if related else None,
        "confirmation": confirmation,
        "notes_count": len(graph_after["nodes"]),
        "session_id": session_id,
    }, 200


def handle_speak(cfg, body):
    """Returns (kind, payload, http_status) where kind is "json" (payload is
    a dict) or "audio" (payload is (bytes, content_type))."""
    text = (body.get("text") or "").strip()
    if not text:
        return "json", {"error": "empty text"}, 400

    providers = get_tts_providers(cfg)
    if not providers:
        return "json", {"error": "no TTS provider configured"}, 404

    for tts in providers:
        try:
            audio, content_type = tts.synthesize(text)
            return "audio", (audio, content_type), 200
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            print(f"[jarvis] {tts.name} TTS failed ({e})", file=sys.stderr)

    return "json", {"error": "tts failed"}, 502
