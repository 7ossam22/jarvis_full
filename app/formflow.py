"""app/formflow.py — deterministic Novatek form-submission routing (zero AI).

Form submission never touches an AI provider: handle_chat calls
try_formflow() BEFORE any model call. When the user's message is a Novatek
form / visit / participant command, the request goes straight to the browser
daemon's rule-engine autopilot (tools/browser_daemon.py) and the reply is
composed from its report — no tokens, no model latency, fully local. Every
other message falls through to the normal AI persona untouched.

The same rule engine is also runnable standalone from the shell:
    python3 tools/novatek_autopilot.py form|visit|takeover
"""
import re

from .connectors.browser import _daemon_alive, execute_browser_tool

# The router fires REAL browser automation (form submission, End Visit — an
# irreversible trial record), so it must only ever match a direct imperative
# command. Every gate below errs toward NOT matching: a missed phrasing just
# reaches the persona (which is policy-bound to delegate to the autopilot),
# while a false positive submits forms nobody asked for.

# Messages that are questions/explanations, not commands — never automate these.
_QUESTION_RE = re.compile(
    r"^\s*(how|what|why|when|where|who|which|should|would|can you explain|"
    r"explain|tell me|do you|did you|is |are )", re.IGNORECASE)

# Negation, deferral, hypotheticals, third-party futures: "don't fill the form
# yet", "the nurse will complete the visit tomorrow", "we might finish all
# visits by friday" — all hard rejections.
_NEGATION_RE = re.compile(
    r"\b(?:don'?t|do\s+not|never|not\s+(?:yet|now)|no\s+need|stop|cancel|"
    r"hold\s+off|wait|later|afterwards?|tomorrow|tonight|next\s+\w+|"
    r"by\s+\w+day|might|maybe|perhaps|planning|plan(?:s|ned)?\s+to|will|"
    r"gonna|going\s+to|if\s+you|before\s+you|instead|remind)\b", re.IGNORECASE)

# Forms that are not Novatek's: "fill out this google form for my survey".
_OFF_TARGET_RE = re.compile(
    r"\b(?:google|survey|typeform|questionnaire|spreadsheet|excel|word|pdf|"
    r"tax|1040|application)\b", re.IGNORECASE)

# Imperative shape: the command verb must sit at the start of the message,
# after at most some politeness/wake-word chatter. "the nurse will complete
# the visit" never matches; "please fill the form" does.
_PREFIX = (r"^\s*(?:(?:please|akira|jarvis|hey|ok(?:ay)?|yes|now|first|next|"
           r"go(?:\s+ahead)?(?:\s+and)?|and|then|just|kindly|can\s+you|"
           r"could\s+you|i\s+(?:want|need)\s+you\s+to|let'?s)[,\s]+)*")

# [\w\s'-]* keeps the verb and its object inside one clause — the match can
# never cross sentence punctuation.
_TAKEOVER_RE = re.compile(
    _PREFIX + r"(?:take\s*over\b[\w\s'-]*\bparticipants?\b"
    r"|(?:complete|finish|do|run)\b[\w\s'-]*\ball\b[\w\s'-]*\bvisits\b"
    r"|run\b[\w\s'-]*\bparticipant\b[\w\s'-]*\bend)\b", re.IGNORECASE)

_VISIT_RE = re.compile(
    _PREFIX + r"(?:complete|finish|close|end|do|submit)\b[\w\s'-]*\bvisits?\b",
    re.IGNORECASE)

_FORM_RE = re.compile(
    _PREFIX + r"(?:fill|complete|submit|answer|finish|autofill|do)\b[\w\s'-]*\bforms?\b",
    re.IGNORECASE)

_VISITS_COUNT_RE = re.compile(r"\b(\d{1,2})\s+visits?\b", re.IGNORECASE)


def detect_intent(question):
    """Returns ('takeover'|'visit'|'form', params) or None."""
    q = (question or "").strip()
    if (not q or "?" in q or _QUESTION_RE.match(q)
            or _NEGATION_RE.search(q) or _OFF_TARGET_RE.search(q)):
        return None
    if _TAKEOVER_RE.search(q):
        m = _VISITS_COUNT_RE.search(q)
        if m:
            visits = max(1, min(20, int(m.group(1))))
        elif re.search(r"\ball\b.*\bvisits\b|\bto the end\b", q, re.IGNORECASE):
            visits = 20
        else:
            visits = 5
        return "takeover", {"max_visits": visits}
    if _VISIT_RE.search(q):
        # One takeover iteration = fill every form, enter the actual date,
        # End Visit — exactly "complete the visit", nothing more.
        return "visit", {"max_visits": 1}
    if _FORM_RE.search(q):
        return "form", {}
    return None


def _sir(cfg):
    return cfg.get("persona.address_term", "sir")


def _summarize_visits(report):
    lines = []
    for v in report.get("visits", []):
        name = v.get("visit") or "visit"
        if v.get("ended"):
            lines.append(f"{name}: all forms submitted ({v.get('progress')}), visit ended.")
        elif v.get("error"):
            lines.append(f"{name}: {v['error']} (progress {v.get('progress')}).")
        unresolved = [u for f in v.get("forms", []) for u in (f.get("unresolved") or [])]
        for u in unresolved[:4]:
            lines.append(f"  unresolved: {u}")
    return lines


def _answer_for(cfg, intent, report):
    sir = _sir(cfg)
    if report.get("error"):
        return f"The form autopilot could not run, {sir}: {report['error']}"

    if intent == "form":
        answered = len(report.get("answered", []))
        progress = report.get("progress_after") or report.get("progress_before") or "?"
        if report.get("submit_verified") and not report.get("unresolved"):
            return (f"Form filled and submitted, {sir} — {answered} answers in "
                    f"{report.get('rounds', '?')} rounds, progress now {progress}.")
        parts = [f"Form run finished incomplete, {sir} — {answered} answers, progress {progress}."]
        for u in (report.get("unresolved") or [])[:5]:
            parts.append(f"Unresolved: {u}.")
        if not report.get("submitted"):
            parts.append("Submit was not reached.")
        return " ".join(parts)

    # visit and takeover both come back as takeover reports
    ended = report.get("visits_completed", 0)
    lines = _summarize_visits(report)
    if intent == "visit":
        if ended >= 1:
            head = f"Visit completed and ended, {sir}."
        elif report.get("status") == "no_next_visit":
            head = f"No visit available to complete, {sir} — nothing pending on this participant."
        else:
            head = f"The visit could not be fully completed, {sir}."
        return " ".join([head] + lines)

    if report.get("status") == "takeover_done":
        head = f"Participant takeover complete, {sir} — {ended} visit(s) finished end to end."
    elif report.get("status") == "no_next_visit":
        head = (f"Takeover finished, {sir} — {ended} visit(s) completed and no further "
                f"visit remains for this participant.")
    else:
        head = f"Takeover stopped early, {sir} — {ended} visit(s) completed."
    return " ".join([head] + lines)


def try_formflow(cfg, question):
    """Deterministic pre-LLM router. Returns {'answer', 'intent', 'report'}
    when the message was a form-submission command (handled locally with zero
    AI), or None to let the normal AI persona take the message."""
    detected = detect_intent(question)
    if not detected:
        return None
    intent, params = detected

    # Never auto-launch a browser off the back of a chat message: act only
    # when the Novatek session is already up. (The daemon's own screen guards
    # then refuse anything that isn't the Visit Mode / profile screen, so
    # even a mis-detected command can never touch a non-Novatek page.)
    if not _daemon_alive():
        return {
            "answer": (f"The browser session isn't running, {_sir(cfg)} — "
                       f"say 'open Novatek' first, then ask me again."),
            "intent": intent,
            "report": {"error": "browser daemon not running"},
        }

    if intent == "form":
        report = execute_browser_tool(cfg, "browser_autofill_form", {})
    else:
        report = execute_browser_tool(
            cfg, "browser_takeover_participant",
            {"max_visits": params.get("max_visits", 5)})

    report.pop("widgets", None)
    return {
        "answer": _answer_for(cfg, intent, report),
        "intent": intent,
        "report": report,
    }
