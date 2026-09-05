"""app/proposals.py — write access to this project, gated on your approval
(Model layer: the change store and the write gate).

Akira can edit its own source, but not on its own say-so. Every change is
staged as a PROPOSAL: a diff you can read, ask about, approve or reject. The
model may write proposals freely; only an approval turns one into bytes on
disk.

The property this file exists to hold:

    THE MODEL CANNOT APPROVE ITS OWN PROPOSAL.

`apply()` is not reachable from any tool. It is called only by the controller,
from a deterministic parse of YOUR message (app/controllers.py), so no amount
of clever output reaches it — not a tool call named "approve", not text that
reads like consent, not a rationale claiming you already agreed. Mistaking the
model's words for the user's is the whole class of bug here, so the split is
structural rather than an instruction the model is asked to honour.

Standard library only, like the rest of this layer.
"""
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Never editable, at any approval level.
#:   - config.json holds live API keys and portal credentials. A diff of it
#:     would print those into the chat log and the browser, which is a leak
#:     whether or not the change is ever approved.
#:   - .git is the undo history this whole feature leans on; a proposal that
#:     can rewrite it can erase the evidence of what it did.
#:   - the rest is machinery, not source: vendored, generated, or a venv.
FORBIDDEN = (
    ".git", ".venv", ".venv-browser", "__pycache__", "node_modules",
    "config.json", "notes/captures", ".jarvis-bak", ".jarvis-tmp",
)

#: Editable, but named out loud before you approve. These decide what Akira is
#: ALLOWED to do — including this gate itself. Not blocked, because you read
#: the diff and judge it, but a change here must never slide past as an
#: ordinary refactor.
SENSITIVE = ("app/proposals.py", "app/persona.py", "app/turn.py",
             "app/controllers.py", "app/connectors/registry.py",
             "config.example.json", ".gitignore", "server.py")

MAX_BYTES = 1024 * 1024
MAX_PENDING = 25

_lock = threading.Lock()
_proposals = {}
_counter = 0


class ProposalError(ValueError):
    """A proposal that must not be staged, or applied, at all."""


# ---- paths -----------------------------------------------------------------

def safe_path(rel_path):
    """(absolute, repo-relative) inside the project, or raise.

    Resolves symlinks BEFORE the containment check: a link inside the project
    pointing at /etc would otherwise pass a test on its own name.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ProposalError("no file path given")

    rel = rel_path.strip()
    if os.path.isabs(rel):
        # Silently reinterpreting "/etc/passwd" as "<project>/etc/passwd" is
        # worse than refusing it: the caller believes it edited one file while
        # a different one is staged.
        raise ProposalError(
            f"{rel_path!r} is an absolute path — give a path relative to the "
            f"project, e.g. 'app/controllers.py'")
    root = os.path.realpath(ROOT)
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ProposalError(f"{rel_path!r} is outside the project")

    rel_norm = os.path.relpath(target, root).replace(os.sep, "/")
    for blocked in FORBIDDEN:
        if rel_norm == blocked or rel_norm.startswith(blocked + "/") or rel_norm.endswith(blocked):
            raise ProposalError(
                f"{rel_norm} is not editable — it holds live secrets, or is "
                f"generated, vendored, or the git history itself")
    return target, rel_norm


def is_sensitive(rel_norm):
    return rel_norm in SENSITIVE


def _looks_binary(sample):
    """Diffs and approvals only mean something for text. A NUL byte is the
    cheap, reliable tell that this is not a file a person can review."""
    return b"\0" in sample[:8192]


def read_file(rel_path):
    """(text, repo-relative path). text is None when the file does not exist."""
    target, rel_norm = safe_path(rel_path)
    if not os.path.isfile(target):
        return None, rel_norm
    with open(target, "rb") as f:
        raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ProposalError(f"{rel_norm} is larger than {MAX_BYTES // 1024} KB")
    if _looks_binary(raw):
        raise ProposalError(f"{rel_norm} is a binary file — there is no diff to review")
    return raw.decode("utf-8", errors="replace"), rel_norm


def _git_files():
    """Everything git considers part of the project: tracked, plus untracked
    files it would not ignore.

    Enumerating with os.walk meant naming every directory to skip, and that
    list rots — a `.venv-kokoro` appeared, was not on it, and buried the source
    tree under 2000 virtualenv files. .gitignore already answers "what belongs
    to this project", so ask the thing that maintains it. Returns None when
    git is unavailable, and the walk below takes over.
    """
    try:
        done = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _walked_files(limit):
    """Fallback enumeration. Skips every dot-directory, which is what the
    hand-written venv list was reaching for and kept missing."""
    root = os.path.realpath(ROOT)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames)
                       if not d.startswith(".") and d not in FORBIDDEN]
        for name in sorted(filenames):
            out.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
            if len(out) >= limit:
                return out
    return out


def _is_forbidden(rel):
    return any(rel == b or rel.startswith(b + "/") or rel.endswith(b)
               for b in FORBIDDEN)


def list_files(subdir="", pattern="*", limit=400):
    """Repo-relative paths under `subdir` matching `pattern`.

    Skips everything FORBIDDEN, so a listing never advertises what cannot be
    read or written.
    """
    prefix = ""
    if subdir and subdir.strip():
        _, prefix = safe_path(subdir)
        prefix = "" if prefix == "." else prefix + "/"

    candidates = _git_files()
    if candidates is None:
        candidates = _walked_files(limit * 20)

    found = []
    for rel in sorted(candidates):
        if prefix and not rel.startswith(prefix):
            continue
        if _is_forbidden(rel):
            continue
        name = rel.rsplit("/", 1)[-1]
        if not (fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern)):
            continue
        found.append(rel)
        if len(found) >= limit:
            break
    return found


def search(query, pattern="*", limit=80):
    """Literal, case-insensitive matches as {path, line, text}. Deliberately
    not a regex: this is reached from model output, and a pathological pattern
    would stall the request thread."""
    if not isinstance(query, str) or not query.strip():
        raise ProposalError("no search text given")
    needle = query.strip().lower()
    hits = []
    for rel in list_files(pattern=pattern, limit=2000):
        try:
            text, _ = read_file(rel)
        except ProposalError:
            continue
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                hits.append({"path": rel, "line": number, "text": line.strip()[:200]})
                if len(hits) >= limit:
                    return hits
    return hits


# ---- staging ---------------------------------------------------------------

def _diff(rel_norm, original, proposed):
    return "".join(difflib.unified_diff(
        (original or "").splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile=f"a/{rel_norm}", tofile=f"b/{rel_norm}", n=3))


def _stats(diff_text):
    added = sum(1 for l in diff_text.splitlines()
                if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_text.splitlines()
                  if l.startswith("-") and not l.startswith("---"))
    return added, removed


def propose(rel_path, rationale, new_content=None, old_string=None, new_string=None):
    """Stages one change and returns its record. Writes nothing."""
    original, rel_norm = read_file(rel_path)

    if new_content is not None:
        proposed = str(new_content)
    elif old_string is not None and new_string is not None:
        if original is None:
            raise ProposalError(f"{rel_norm} does not exist, so there is nothing to replace")
        hits = original.count(old_string)
        if hits == 0:
            raise ProposalError(f"that exact text does not appear in {rel_norm}")
        if hits > 1:
            # Guessing which occurrence was meant is how an edit lands in the
            # wrong function and the diff still looks plausible.
            raise ProposalError(
                f"that text appears {hits} times in {rel_norm} — include more "
                f"surrounding lines so it matches exactly once")
        proposed = original.replace(old_string, new_string)
    else:
        raise ProposalError("give either new_content, or both old_string and new_string")

    if len(proposed.encode("utf-8")) > MAX_BYTES:
        raise ProposalError(f"the result would be larger than {MAX_BYTES // 1024} KB")
    if proposed == original:
        raise ProposalError(f"that would change nothing in {rel_norm}")

    diff_text = _diff(rel_norm, original, proposed)
    added, removed = _stats(diff_text)

    global _counter
    with _lock:
        waiting = sum(1 for p in _proposals.values() if p["status"] == "pending")
        if waiting >= MAX_PENDING:
            raise ProposalError(
                f"{waiting} changes are already waiting — approve or reject some "
                f"before proposing more")
        _counter += 1
        record = {
            "id": str(_counter),
            "path": rel_norm,
            "rationale": str(rationale or "").strip()[:600] or "(no reason given)",
            "original": original,
            "proposed": proposed,
            "diff": diff_text,
            "added": added,
            "removed": removed,
            "is_new_file": original is None,
            "sensitive": is_sensitive(rel_norm),
            "created": time.time(),
            "status": "pending",
            "backup": None,
            "error": None,
        }
        _proposals[record["id"]] = record
    return record


# ---- reading ---------------------------------------------------------------

def _public(record, with_diff=True):
    out = {k: record[k] for k in
           ("id", "path", "rationale", "added", "removed", "is_new_file",
            "sensitive", "status", "error")}
    out["age"] = round(time.time() - record["created"], 1)
    if with_diff:
        out["diff"] = record["diff"]
    return out


def get(proposal_id):
    with _lock:
        return _proposals.get(str(proposal_id).strip())


def pending(with_diff=True):
    with _lock:
        records = [p for p in _proposals.values() if p["status"] == "pending"]
    records.sort(key=lambda p: p["created"])
    return [_public(p, with_diff) for p in records]


def recent(limit=12, with_diff=False):
    with _lock:
        records = sorted(_proposals.values(), key=lambda p: p["created"], reverse=True)
    return [_public(p, with_diff) for p in records[:limit]]


# ---- the gate --------------------------------------------------------------

def apply(proposal_id):
    """Writes an approved change to disk. Returns the updated record.

    NOT reachable from any tool — see the module docstring. The caller must
    have established that a HUMAN approved this specific id.
    """
    record = get(proposal_id)
    if record is None:
        raise ProposalError(f"there is no change #{proposal_id}")
    if record["status"] != "pending":
        raise ProposalError(f"change #{proposal_id} was already {record['status']}")

    target, rel_norm = safe_path(record["path"])   # re-checked at write time

    try:
        current, _ = read_file(rel_norm)
        if current != record["original"]:
            # The file moved under the proposal. Applying now would silently
            # revert whatever else changed it, so the diff you approved is no
            # longer the diff that would land.
            raise ProposalError(
                f"{rel_norm} has changed since this was proposed — reject it and "
                f"ask for a fresh diff")

        backup = None
        if os.path.isfile(target):
            backup = target + ".jarvis-bak"
            shutil.copy2(target, backup)

        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Write-then-rename: an interrupted write must not leave half a file
        # where a working module used to be.
        tmp = target + ".jarvis-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(record["proposed"])
        os.replace(tmp, target)
    except (OSError, ProposalError) as e:
        with _lock:
            record["status"] = "failed"
            record["error"] = str(e)
        raise

    with _lock:
        record["status"] = "applied"
        record["backup"] = backup
    return record


def reject(proposal_id, reason=""):
    record = get(proposal_id)
    if record is None:
        raise ProposalError(f"there is no change #{proposal_id}")
    if record["status"] != "pending":
        raise ProposalError(f"change #{proposal_id} was already {record['status']}")
    with _lock:
        record["status"] = "rejected"
        record["error"] = str(reason or "")[:200] or None
    return record


# ---- reading the USER's decision -------------------------------------------
# Parsed here, in Python, from the user's own message — never inferred by the
# model. This is the half of the gate that decides whether apply() is called at
# all, so it must not be reachable through anything the model can write.

# Two tiers, because the failure modes are not symmetric.
#
# An explicit instruction ("approve", "apply it", "go ahead") means what it
# says wherever it appears in a sentence. A bare agreement ("yes", "sure",
# "ok") means it only when it is the WHOLE message: "what a lovely day, sure
# is nice" is not consent to write files, and treating it as consent is the
# one bug here that silently modifies the project. Observed exactly that way
# in testing before this split existed.
_APPROVE_STRONG_RE = re.compile(
    r"\b(approve[ds]?|apply (it|that|them|all|the change)|accept (it|that|them)"
    r"|go ahead|make it so|ship it|commit (it|that)|do it)\b", re.IGNORECASE)
_APPROVE_BARE_RE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|approved|apply|accept|proceed|please)"
    r"[\s,.!]*(please|sir|go|do it|then)?[\s,.!]*$", re.IGNORECASE)

_REJECT_STRONG_RE = re.compile(
    r"\b(reject|discard|scrap (it|that)|drop (it|that)|undo that|throw it away"
    r"|don'?t (apply|approve|do)|do not (apply|approve|do)|never ?mind)\b",
    re.IGNORECASE)
_REJECT_BARE_RE = re.compile(
    r"^(no|nope|nah|cancel|reject|discard|stop)[\s,.!]*"
    r"(thanks|thank you|please|sir|don'?t)?[\s,.!]*$", re.IGNORECASE)

_SHOW_RE = re.compile(
    r"\b(what('?s| is| are| would)?.{0,24}(pending|waiting|change|diff)"
    r"|show.{0,16}(change|diff|pending)|list.{0,16}(change|pending)"
    r"|explain.{0,16}(change|diff)|what does it (change|do))\b", re.IGNORECASE)

_ALL_RE = re.compile(r"\b(all|everything|both)\b", re.IGNORECASE)
_ID_RE = re.compile(r"(?:#|\bchange\s+|\bnumber\s+|\bno\.?\s*)(\d{1,4})\b", re.IGNORECASE)


def parse_decision(text):
    """What the USER said about staged changes, or None.

    Returns {"action": "approve"|"reject"|"show", "ids": [...] | "all" | None}.

    Only reports what the words say. The caller additionally requires that
    something is actually waiting, so a stray "yes" with an empty queue does
    nothing at all.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()

    ids = _ID_RE.findall(stripped)
    scope = "all" if _ALL_RE.search(stripped) else (ids or None)

    # Rejection is tested first: "do not apply it" and "don't approve that"
    # both contain an approval word, and reading either as consent is the one
    # mistake here that writes to disk.
    if _REJECT_STRONG_RE.search(stripped) or _REJECT_BARE_RE.match(stripped):
        return {"action": "reject", "ids": scope}
    if _SHOW_RE.search(stripped):
        return {"action": "show", "ids": scope}
    if _APPROVE_STRONG_RE.search(stripped) or _APPROVE_BARE_RE.match(stripped):
        return {"action": "approve", "ids": scope}
    return None


def clear_all():
    """Test hook."""
    global _counter
    with _lock:
        _proposals.clear()
        _counter = 0
