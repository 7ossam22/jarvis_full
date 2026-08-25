"""app/history.py — in-memory per-session conversation history (Model layer).

Lives only in the running process, same as the original design: no
persistence across restarts. max_turns is now caller-supplied (from
config's retrieval.max_history_turns) instead of a module constant.
"""

SESSIONS = {}


def get_history(session_id):
    return SESSIONS.setdefault(session_id, [])


def append_history(session_id, role, content, max_turns=6):
    hist = get_history(session_id)
    hist.append({"role": role, "content": content})
    del hist[: max(0, len(hist) - max_turns * 2)]
