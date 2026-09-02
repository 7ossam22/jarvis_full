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
import contextvars
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

#: The chat job running on this thread, so code deep inside a provider's tool
#: loop can report progress without every layer between here and there having
#: to pass an id down. Each worker thread starts with its own empty context, so
#: two concurrent chats cannot see each other's.
_current_job = contextvars.ContextVar("jarvis_job_id", default=None)


def current_job():
    return _current_job.get()


def bind_job(job_id):
    """Marks this thread as working on `job_id`. Called once, at the top of the
    worker thread."""
    _current_job.set(job_id)


def activity(message, notable=False, job_id=None):
    """Say what is happening RIGHT NOW in the running turn.

    Distinct from record(): events are a history worth scrolling, this is a
    single replaceable line answering "why is nothing happening yet". A long
    tool-using turn is otherwise completely silent from the outside, which is
    exactly how waiting on a rate limit and hanging forever look identical.

    `notable` marks the ones a person would want SAID out loud rather than
    merely shown — a stall, a failure, a wait long enough to worry about. Most
    progress is not notable; narrating every tool call would be unbearable.
    """
    job_id = job_id or _current_job.get()
    if job_id is None:
        return
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["activity"] = str(message)[:200]
            job["activity_at"] = time.time()
            if notable:
                job["notable_seq"] = job.get("notable_seq", 0) + 1


def job_activity(job_id):
    """What that job is doing now, for the viewer's poll. `seq` changes only on
    notable updates, so the client can speak those and stay quiet otherwise."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            "activity": job.get("activity"),
            "seconds": round(time.time() - job["started"], 1),
            "notable_seq": job.get("notable_seq", 0),
            "stuck": (time.time() - job["started"]) > STUCK_AFTER_S,
        }


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
                         "finished": None, "ok": None, "error": None,
                         "activity": None, "activity_at": None, "notable_seq": 0}


def job_finished(job_id, ok=True, error=None):
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update({"finished": time.time(), "ok": bool(ok),
                        "error": None if error is None else str(error)[:400]})


def _running_jobs_locked(now):
    return [
        {"what": j["what"], "seconds": round(now - j["started"], 1),
         "activity": j.get("activity"),
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
