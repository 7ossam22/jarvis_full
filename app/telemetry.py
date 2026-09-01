"""app/telemetry.py — what the system is actually doing, and what went wrong
(Model layer: diagnostics).

Failures used to exist only as stderr lines nobody was reading. A provider
that fell over, a tool that returned an error, a chat job that never finished
— all of it was invisible from the browser, and the user's only signal was an
answer that quietly omitted the problem, or claimed success that had not
happened.

This module is the single place those events are recorded, so the viewer can
show them (GET /status) and so an error can never be silently discarded on the
way to the user. It is deliberately tiny: a bounded in-memory ring buffer, no
persistence, no dependencies. Losing it on restart is fine — it describes the
running process, not history worth keeping.

Thread-safe: the server is a ThreadingHTTPServer and chat runs on worker
threads, so several of these can be written at once.
"""
import threading
import time

#: Kept small on purpose. This is a live view of the current run, not a log
#: file — the viewer shows the newest handful and nobody scrolls 500 back.
MAX_EVENTS = 120

#: A chat job still running after this long is almost certainly stuck rather
#: than slow. A big Novatek form legitimately takes minutes, so this is
#: generous; it only drives a warning, never an abort.
STUCK_AFTER_S = 180

_lock = threading.Lock()
_events = []
_jobs = {}


def record(kind, message, detail=None):
    """Record one event.

    Args:
        kind: short category — "error", "warning", "tool", "provider", "job",
            "info". The viewer colours on this.
        message: one line, written for a person, not a log parser.
        detail: optional extra context (a dict or a string), shown on demand.
    """
    with _lock:
        _events.append({
            "at": time.time(),
            "kind": str(kind),
            "message": str(message)[:400],
            "detail": detail if detail is None else str(detail)[:600],
        })
        del _events[: max(0, len(_events) - MAX_EVENTS)]


def job_started(job_id, what):
    """Note that a long-running unit of work began."""
    with _lock:
        _jobs[job_id] = {"what": str(what)[:200], "started": time.time(),
                         "finished": None, "ok": None, "error": None}


def job_finished(job_id, ok=True, error=None):
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update({"finished": time.time(), "ok": bool(ok),
                        "error": None if error is None else str(error)[:400]})


def _running_jobs_locked(now):
    return [
        {"what": j["what"], "seconds": round(now - j["started"], 1),
         "stuck": (now - j["started"]) > STUCK_AFTER_S}
        for j in _jobs.values() if j["finished"] is None
    ]


def snapshot(limit=25):
    """Everything the status panel needs, in one read.

    Returns running work (with a `stuck` flag), the most recent events newest
    first, and a count of errors so the panel can shout without parsing.
    """
    now = time.time()
    with _lock:
        running = _running_jobs_locked(now)
        recent = list(_events[-limit:])[::-1]
        errors = sum(1 for e in _events if e["kind"] == "error")
        # Forget jobs that finished a while ago so _jobs cannot grow forever.
        for job_id in [k for k, j in _jobs.items()
                       if j["finished"] and now - j["finished"] > 900]:
            del _jobs[job_id]
    return {
        "now": now,
        "running": running,
        "busy": bool(running),
        "stuck": any(j["stuck"] for j in running),
        "error_count": errors,
        "events": [dict(e, ago=round(now - e["at"], 1)) for e in recent],
    }
