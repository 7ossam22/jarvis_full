"""app/controllers.py — request handling logic (Controller layer).

Each function takes plain data in and returns plain data out — no HTTP
specifics (no self.send_response, no headers). app/http_server.py adapts
these to actual HTTP requests/responses. This separation is what makes the
business logic here testable/reusable independent of the transport.
"""
import base64
import os
import re
import sys
import urllib.error
import uuid

from . import history, persona, retrieval, telemetry
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


def _chat_reply(answer, session_id, **extra):
    """The shape every /chat response has, so a short-circuit reply is
    indistinguishable to the viewer from a model-generated one."""
    payload = {"answer": answer, "nodes": [], "session_id": session_id,
               "image_urls": [], "video_urls": [], "show_url": None,
               "jira_data": None, "screenshot_url": None, "errors": []}
    payload.update(extra)
    return payload, 200


def _decide_staged_changes(question, session_id):
    """Applies or rejects staged code changes on the user's say-so.

    Returns a finished (response, status) when the message WAS a decision, and
    None when it was ordinary conversation that should go to the model.

    Bare "yes" / "no" only count while something is actually waiting —
    otherwise every casual agreement in conversation would write files.
    """
    from . import proposals

    waiting = proposals.pending(with_diff=False)
    if not waiting:
        return None
    decision = proposals.parse_decision(question)
    if decision is None:
        return None

    scope = decision["ids"]
    if scope == "all":
        targets = [p["id"] for p in waiting]
    elif scope:
        targets = scope
    elif len(waiting) == 1:
        targets = [waiting[0]["id"]]
    else:
        # Never guess which of several changes was meant: approving the wrong
        # diff writes the wrong file, and the user believes otherwise.
        listing = ", ".join(f"#{p['id']} ({p['path']})" for p in waiting)
        return _chat_reply(
            f"Which one, sir? {len(waiting)} are waiting: {listing}.",
            session_id, pending_changes=waiting)

    if decision["action"] == "show":
        return None          # let the model explain it, with the diffs in context

    applied, rejected, problems = [], [], []
    for change_id in targets:
        try:
            if decision["action"] == "approve":
                record = proposals.apply(change_id)
                applied.append(f"#{record['id']} {record['path']} "
                               f"(+{record['added']}/-{record['removed']})")
                telemetry.record("info", f"applied change #{record['id']}", record["path"])
            else:
                record = proposals.reject(change_id, question[:120])
                rejected.append(f"#{record['id']} {record['path']}")
        except proposals.ProposalError as e:
            problems.append(str(e))
            telemetry.record("error", f"change #{change_id} could not be applied", e)

    parts = []
    if applied:
        parts.append("Written to disk: " + "; ".join(applied)
                     + ". Restart the server for it to take effect.")
    if rejected:
        parts.append("Discarded: " + "; ".join(rejected) + ".")
    if problems:
        # Surfaced, never swallowed: a failed write that reads as success is
        # the exact failure this whole flow exists to prevent.
        parts.append("Could not do it: " + "; ".join(problems))
    return _chat_reply(" ".join(parts) or "Nothing to do, sir.", session_id,
                       pending_changes=proposals.pending(with_diff=False),
                       errors=problems)


def handle_chat(cfg, notes_dir, viewer_dir, body):
    """Returns (response_dict, http_status)."""
    question = (body.get("message") or "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())
    if not question:
        return {"error": "empty message"}, 400

    # Approving a staged code change happens HERE, before the model is called
    # and without consulting it. The model can stage diffs; only this parse of
    # the user's own words can write one to disk, so nothing the model emits —
    # a tool call, a confident "shall I apply it?", a rationale claiming prior
    # consent — can reach the file system. See app/proposals.py.
    verdict = _decide_staged_changes(question, session_id)
    if verdict is not None:
        return verdict

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

    # Sight. Frames reach the model as part of THIS turn rather than as a tool
    # result, because a tool result is JSON text — a model cannot look at a
    # JPEG described to it in a string. Two sources, never mixed:
    #   - the browser sends frames from the camera on the device showing the
    #     interface (the default: "look at me", "what do you see"),
    #   - the server grabs one from its own webcam when the user says "remote".
    from . import turn, vision
    turn.bind_message(question)

    frames = vision.normalize_images(body.get("images"))
    camera_note = None
    if turn.wants_remote_camera(question):
        from .connectors import camera
        shot = camera.capture_frame()
        if shot["ok"]:
            frames = vision.normalize_images(
                [{"media_type": "image/jpeg",
                  "data": base64.b64encode(shot["jpeg"]).decode("ascii")}])
            camera_note = f"[live frame from the machine's camera, {shot['device']}]"
        else:
            # Say it in the turn itself. A failed capture that only lands in a
            # log produces an answer describing a scene that was never seen.
            camera_note = f"[the machine's camera could not be read: {shot['error']}]"
            telemetry.record("error", "machine camera capture failed", shot["error"])
    elif frames:
        camera_note = f"[live frame{'s' if len(frames) > 1 else ''} from the user's camera, just captured]"

    if camera_note:
        user_content = f"{camera_note}\n{user_content}"

    last_turn = {"role": "user", "content": user_content}
    if frames:
        last_turn["images"] = frames
    messages = hist + [last_turn]

    fallback = persona.no_brain_apology(cfg)
    if relevant:
        fallback += f" By keyword match alone, the closest note is '{relevant[0]['label']}'."

    import time
    from .connectors.jira import get_last_jira_result

    from . import telemetry

    t0 = time.time()
    system_prompt = persona.build_system_prompt(cfg)
    # `model.vision_provider` picks which backend does the LOOKING, separately
    # from the one that does the talking — the same split as
    # `model.assist_provider`, and for the same reason: a local multimodal
    # model can describe a camera frame all day without touching a metered
    # quota, while the conversation still goes wherever `model.provider` says.
    # Unset, vision follows `model.provider` like everything else, and failover
    # is unchanged either way: preferring a backend that is down or blind still
    # reaches another one that can see.
    answer = call_model(cfg, system_prompt, messages, fallback,
                        needs_vision=bool(frames),
                        prefer=cfg.get("model.vision_provider") if frames else None)

    # Errors recorded during THIS turn travel back with the answer. The model
    # is told to report them, but a small model narrates optimistically — it
    # offered to end a visit that had not been filled — so the failure is also
    # returned as data the interface can show regardless of what was said.
    turn_errors = [e["message"] + (f" — {e['detail']}" if e.get("detail") else "")
                   for e in telemetry.snapshot(limit=40)["events"]
                   if e["kind"] == "error" and e["at"] >= t0]
    max_images = cfg.get("images.max_gallery", 6)
    answer, image_urls, video_urls, source_urls, show_url = extract_media_references(answer, max_images)

    # Check if Jira was interacted with during this turn
    last_jira = get_last_jira_result()
    jira_data = None
    if last_jira and last_jira.get("timestamp", 0) >= (t0 - 1.0):
        jira_data = last_jira

    from .connectors.system import get_last_screenshot

    # Check if a screenshot was taken during this turn
    last_screen = get_last_screenshot()
    screenshot_url = None
    if last_screen and last_screen.get("timestamp", 0) >= (t0 - 1.0):
        screenshot_url = last_screen.get("url")

    # The spoken/displayed answer never contains URLs (persona rule — TTS would
    # read them as gibberish), but later turns like "send that to Discord" need
    # them, so stash them in the history copy only as a reference footnote.
    hist_answer = answer
    ref_links = source_urls + video_urls + image_urls
    if screenshot_url:
        ref_links.insert(0, screenshot_url)
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
        "jira_data": jira_data,
        "screenshot_url": screenshot_url,
        "errors": turn_errors,
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


def handle_embeddable(cfg, body):
    """Returns (response_dict, http_status) for GET/POST /embeddable.

    The viewer's SHOW window renders a page in an iframe, but many sites send
    `X-Frame-Options: DENY` or `Content-Security-Policy: frame-ancestors 'none'`
    and Chrome then paints a blank broken-document box. That refusal is
    invisible to the page doing the embedding — a cross-origin iframe reports
    nothing readable either way — so the browser cannot tell "still loading"
    from "refused" and used to just look broken.

    The server has no such restriction: it can read the headers itself. This
    endpoint reports one of three verdicts so the viewer can choose what to
    render:

      embeddable "yes"     -> iframe it, as before.
      embeddable "no"      -> the site explicitly refuses framing. `readable`
                              carries extracted text so the window can show the
                              content anyway, when it could be fetched.
      embeddable "unknown" -> we could not tell (the site blocks server-side
                              requests too — Cloudflare returns 403 to anything
                              that is not a real browser). The viewer should
                              still try the iframe, since a real browser often
                              succeeds where this probe cannot.
    """
    from .connectors.web_fetch import (
        UnsafeURLError, _assert_fetchable, execute_web_fetch, USER_AGENT,
    )
    import urllib.error
    import urllib.request

    url = (body.get("url") or "").strip()
    if not url:
        return {"error": "empty url"}, 400

    # Same guard the web_fetch tool uses: this endpoint takes a URL from the
    # browser, so it is exactly as exposed as the tool is.
    try:
        _assert_fetchable(url)
    except UnsafeURLError as e:
        return {"embeddable": "no", "reason": str(e), "readable": None}, 200

    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            xfo = (resp.headers.get("X-Frame-Options") or "").strip().lower()
            csp = (resp.headers.get("Content-Security-Policy") or "").lower()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        # Probe blocked or failed. Not a verdict — let the browser try.
        return {"embeddable": "unknown", "reason": str(e), "readable": None}, 200

    refuses = "deny" in xfo or "sameorigin" in xfo
    if "frame-ancestors" in csp:
        directive = csp.split("frame-ancestors", 1)[1].split(";", 1)[0]
        # 'none' or a host allowlist that cannot include us — either way this
        # origin is not permitted to frame it.
        refuses = refuses or "'none'" in directive or "*" not in directive

    if not refuses:
        return {"embeddable": "yes", "reason": "", "readable": None}, 200

    # Refused: fetch the text so the window has something to show.
    fetched = execute_web_fetch({"url": url})
    readable = None
    if fetched.get("status") == "ok" and fetched.get("content"):
        readable = {"title": fetched.get("title", ""), "content": fetched["content"]}
    return {
        "embeddable": "no",
        "reason": f"the site sends {'X-Frame-Options: ' + xfo if xfo else 'a frame-ancestors policy'}",
        "readable": readable,
    }, 200


# --- LLM assist for the deterministic form autopilot ------------------------
# The autopilot (tools/browser_daemon.py) fills Novatek forms with zero AI: it
# is fast, repeatable, and free. But its knowledge of *why* a field was refused
# is a list of substrings, so a phrasing nobody anticipated leaves it guessing
# down a ladder of defaults. This endpoint is the escape hatch: when the rules
# are exhausted on ONE question, the daemon asks here, a model reads the actual
# validation message, and answers with a single structured action.
#
# Fallback, never driver. It is consulted per stuck question, not per form, so
# a normal fill still makes zero model calls.

FORM_ASSIST_SYSTEM = (
    "You interpret a validation error from one question of a clinical-trial web form "
    "and decide the single next action. Reply with RAW JSON only, no prose, no code "
    "fences.\n\n"
    'Shape: {"action": "type"|"click"|"skip", "value": "<text to type>", '
    '"label": "<exact label to click>", "reason": "<8 words>"}\n\n'
    "Rules:\n"
    "- \"type\": give `value` — the literal text to put in the field. Obey the error "
    "exactly: if it names a format like (M/d/yyyy) produce that shape; if it says "
    "letters only give letters; if it says numbers only give digits.\n"
    "- \"click\": give `label`, copied EXACTLY from the options offered. Nothing else.\n"
    "- \"skip\": only when no action could plausibly help.\n"
    "- Values already tried are listed; never repeat one.\n"
    "- This is test data — any plausible valid value is fine. Prefer the simplest."
)


def handle_form_assist(cfg, body):
    """Returns (response_dict, http_status).

    Body: {"question": str, "error": str, "fields": [...], "options": [...],
           "tried": [...]}. Response: {"action", "value", "label", "reason"}.

    Never raises and never returns a non-answer: on any failure it replies
    {"action": "skip"}, so a model outage degrades the autopilot back to its
    deterministic behaviour instead of stalling it.
    """
    import json as _json

    question = (body.get("question") or "").strip()
    error = (body.get("error") or "").strip()
    if not question:
        return {"action": "skip", "reason": "no question given"}, 400

    options = [str(o) for o in (body.get("options") or [])][:12]
    tried = [str(t) for t in (body.get("tried") or [])][:8]
    prompt = (
        f"QUESTION: {question}\n"
        f"VALIDATION ERROR: {error or '(none — the field simply would not accept input)'}\n"
        f"FIELD TYPES PRESENT: {', '.join(body.get('fields') or ['textbox'])}\n"
        f"CLICKABLE OPTIONS: {options if options else '(none)'}\n"
        f"ALREADY TRIED AND REFUSED: {tried if tried else '(nothing yet)'}\n\n"
        "What is the single next action?"
    )

    # The autopilot escalates here only when its deterministic rules run out on
    # one question — but a stuck visit produces a burst of these, dozens in a
    # couple of minutes. That burst is what exhausts a per-minute API quota
    # (a free-tier Gemini key allows ~10 requests/minute), and the run then
    # dies on a rate limit rather than on the problem it was actually solving.
    # `model.assist_provider` points this one path at a backend with no such
    # ceiling — typically the local LM Studio model, which is more than capable
    # of a single constrained-JSON decision. Unset, it simply follows
    # `model.provider` like everything else, and failover is unchanged either
    # way, so a preferred backend being down still gets the question answered.
    raw = call_model(cfg, FORM_ASSIST_SYSTEM, [{"role": "user", "content": prompt}], "",
                     prefer=cfg.get("model.assist_provider"))
    if not raw or "{" not in raw:
        return {"action": "skip", "reason": "no usable model reply"}, 200

    try:
        parsed = _json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except (ValueError, TypeError):
        return {"action": "skip", "reason": "model reply was not JSON"}, 200

    action = str(parsed.get("action") or "").strip().lower()
    if action not in ("type", "click", "skip"):
        return {"action": "skip", "reason": "unknown action"}, 200

    # Constrain the model to what the page actually offers: a click must name
    # an option that exists, and a typed value must be short and plain. The
    # daemon executes this, so it is not a place to trust free-form output.
    if action == "click":
        label = str(parsed.get("label") or "").strip()
        match = next((o for o in options if o.strip().lower() == label.lower()), None)
        if not match:
            return {"action": "skip", "reason": f"'{label[:30]}' is not an offered option"}, 200
        return {"action": "click", "label": match, "reason": str(parsed.get("reason", ""))[:60]}, 200

    if action == "type":
        value = str(parsed.get("value") or "").strip()
        if not value or len(value) > 100 or "\n" in value:
            return {"action": "skip", "reason": "unusable value"}, 200
        if value in tried:
            return {"action": "skip", "reason": "model repeated a refused value"}, 200
        return {"action": "type", "value": value, "reason": str(parsed.get("reason", ""))[:60]}, 200

    return {"action": "skip", "reason": str(parsed.get("reason", ""))[:60]}, 200


def handle_status(cfg):
    """Everything the viewer's system panel shows — GET /status.

    Answers "what is going on right now, and what has gone wrong", which the
    app previously could not answer at all: a failure existed only as a stderr
    line, so from the browser a broken run and a working one looked identical.

    Reports live work (with a stuck flag), recorded errors, which model backend
    is actually in use, how full the conversation is getting, and any config
    problem that will bite later. Never raises — a diagnostics panel that
    500s when something is wrong is worse than none.
    """
    from . import history, telemetry
    from .connectors.registry import registry
    from .providers.llm import get_llm_providers

    snap = telemetry.snapshot()

    problems = []
    try:
        providers = [p.name for p in get_llm_providers(cfg)]
    except Exception as e:                                   # noqa: BLE001
        providers, _ = [], problems.append(f"provider setup failed: {e}")
    if not providers:
        problems.append("no model backend is configured — JARVIS cannot answer at all")

    chosen = (cfg.get("model.provider") or "").strip().lower()
    if chosen and chosen not in providers:
        problems.append(
            f"model.provider is '{chosen}' but that backend is not usable; "
            f"{'falling back to ' + providers[0] if providers else 'nothing is'}")

    # Conversation size: the practical cause of "it forgot" and of slow turns.
    max_turns = cfg.get("retrieval.max_history_turns", 6)
    sessions = []
    for session_id, turns in list(history.SESSIONS.items()):
        chars = sum(len(str(t.get("content", ""))) for t in turns)
        sessions.append({
            "session": session_id[:8],
            "turns": len(turns),
            "chars": chars,
            "full": len(turns) >= max_turns * 2,
        })
    if any(s["full"] for s in sessions):
        problems.append(
            f"a conversation has hit its {max_turns}-turn limit — older turns are being "
            "dropped, so earlier context is already gone")

    # A tool that keeps failing is worth naming before the user asks.
    failing = {}
    for event in snap["events"]:
        if event["kind"] == "error" and event["message"].startswith("tool "):
            failing[event["message"]] = failing.get(event["message"], 0) + 1
    for message, count in failing.items():
        if count >= 2:
            problems.append(f"repeated failure: {message} ({count}x)")

    return {
        "busy": snap["busy"],
        "stuck": snap["stuck"],
        "running": snap["running"],
        "error_count": snap["error_count"],
        "events": snap["events"],
        "providers": providers,
        "provider_in_use": providers[0] if providers else None,
        "model": (cfg.get("model.lmstudio_model_id") if providers[:1] == ["lmstudio"]
                  else cfg.get("model.model_id")),
        "tools": len(registry),
        "sessions": sessions,
        "problems": problems,
    }
