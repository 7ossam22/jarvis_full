"""app/connectors/project.py — the tools that let a model read and change this
project's own source.

Reading is direct. Writing is not: `project_propose_edit` stages a diff and
returns its id, and nothing reaches disk until the user approves it in their
own words. See app/proposals.py for why that split is structural rather than a
rule the model is asked to follow.

Registered for EVERY provider — Anthropic, Gemini, LM Studio, and the AGY and
Claude CLI backends that borrow those two schemas. The approval gate, not the
provider list, is what makes this safe, so restricting it by backend would buy
nothing and would mean the assistant could edit its own code on one model and
not another.
"""
from .. import proposals

#: Big enough for a whole module, small enough that a runaway read cannot
#: swamp the model's context and push the actual question out of it.
READ_LIMIT = 60_000


def get_project_tools():
    return [
        {
            "name": "project_list_files",
            "description": (
                "List the source files of this project (the Akira assistant you are "
                "part of). Use this to find your way around before reading or editing. "
                "Respects .gitignore, so virtualenvs and caches never appear."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "subdir": {"type": "string",
                               "description": "Limit to a directory, e.g. 'app' or 'viewer/js'. Omit for the whole project."},
                    "pattern": {"type": "string",
                                "description": "Filename glob, e.g. '*.py' or '*provider*'. Default '*'."},
                },
                "required": [],
            },
        },
        {
            "name": "project_search",
            "description": (
                "Find literal text anywhere in this project's source, returning file "
                "and line number. Use it to locate the code you need before reading a "
                "whole file. Case-insensitive; plain text, not a regex."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to find, e.g. 'def handle_chat'."},
                    "pattern": {"type": "string", "description": "Limit to files matching this glob, e.g. '*.py'."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "project_read_file",
            "description": (
                "Read one source file of this project. ALWAYS read a file before "
                "proposing a change to it — an edit written from memory of what the "
                "code probably says is how a plausible-looking diff breaks a working "
                "module."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Project-relative path, e.g. 'app/controllers.py'."},
                },
                "required": ["path"],
            },
        },
        {
            "name": "project_propose_edit",
            "description": (
                "Propose a change to one file of this project. This does NOT write "
                "anything: it stages a diff for the user to approve, reject or ask "
                "about, and returns the change number. Tell the user the number and "
                "what it does, then STOP and wait — you cannot approve it yourself, "
                "and saying it is done before they approve is a false report. "
                "Give either new_content (the complete new file) or the old_string/"
                "new_string pair (old_string must appear exactly once, so include "
                "enough surrounding lines to be unambiguous). Read the file first."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project-relative path to change or create."},
                    "rationale": {"type": "string",
                                  "description": "Why this change, in one or two sentences. The user reads this beside the diff."},
                    "new_content": {"type": "string", "description": "The complete new contents of the file."},
                    "old_string": {"type": "string", "description": "Exact existing text to replace (must be unique in the file)."},
                    "new_string": {"type": "string", "description": "Replacement for old_string."},
                },
                "required": ["path", "rationale"],
            },
        },
        {
            "name": "project_pending_changes",
            "description": (
                "List the changes you have proposed that are still waiting for the "
                "user's decision, with their numbers and diffs. Use it when the user "
                "asks what is pending, or what a change would do."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
    ]


def execute_project_tool(cfg, tool_name, tool_input):
    """Every failure comes back as data the model can relay — a raised
    exception here would kill the turn with no answer.

    `cfg` is unused: these tools read and stage files, and take nothing from
    configuration. It is in the signature because that is the shape every
    connector bundle is called with.
    """
    del cfg
    try:
        if tool_name == "project_list_files":
            files = proposals.list_files(
                subdir=str(tool_input.get("subdir") or ""),
                pattern=str(tool_input.get("pattern") or "*"))
            return {"status": "ok", "count": len(files), "files": files}

        if tool_name == "project_search":
            hits = proposals.search(
                str(tool_input.get("query") or ""),
                pattern=str(tool_input.get("pattern") or "*"))
            return {"status": "ok", "count": len(hits), "matches": hits}

        if tool_name == "project_read_file":
            text, rel = proposals.read_file(str(tool_input.get("path") or ""))
            if text is None:
                return {"status": "error", "error": f"{rel} does not exist"}
            truncated = len(text) > READ_LIMIT
            return {"status": "ok", "path": rel, "truncated": truncated,
                    "lines": text.count("\n") + 1,
                    "content": text[:READ_LIMIT]}

        if tool_name == "project_propose_edit":
            record = proposals.propose(
                str(tool_input.get("path") or ""),
                tool_input.get("rationale"),
                new_content=tool_input.get("new_content"),
                old_string=tool_input.get("old_string"),
                new_string=tool_input.get("new_string"))
            return {
                "status": "awaiting_approval",
                "change_number": record["id"],
                "path": record["path"],
                "added": record["added"],
                "removed": record["removed"],
                "sensitive": record["sensitive"],
                "diff": record["diff"][:8000],
                # Spelled out because a tool result reading "ok" is exactly what
                # a model turns into "done, sir" in the next sentence.
                "note": (f"NOTHING HAS BEEN WRITTEN. Change #{record['id']} is staged and "
                         f"waiting for the user to approve or reject it. Tell them the "
                         f"number and what it changes. Do not claim it is applied."),
            }

        if tool_name == "project_pending_changes":
            waiting = proposals.pending(with_diff=True)
            return {"status": "ok", "count": len(waiting), "pending": waiting}

        return {"status": "error", "error": f"unknown project tool {tool_name!r}"}
    except proposals.ProposalError as e:
        return {"status": "error", "error": str(e)}
    except OSError as e:
        return {"status": "error", "error": f"file system error: {e}"}
