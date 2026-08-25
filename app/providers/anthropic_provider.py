"""app/providers/anthropic_provider.py — model calling (Model layer):
direct Anthropic API, then `claude -p` CLI fallback, then a canned response.
Moved verbatim from server.py; cfg is now an app.config.Config instance
instead of a flat dict.
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request


def call_anthropic(cfg, system_prompt, messages):
    api_key = cfg.get("model.provider_api_key")
    model = cfg.get("model.model_id") or "claude-sonnet-5"
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
    # --system-prompt fully replaces Claude Code's own default identity for this
    # call — without it, JARVIS's persona/instructions were just extra text inside
    # the user turn, competing with (and losing to) Claude Code's real system
    # prompt, which genuinely claims to have no visual output.
    # --allowedTools WebSearch,WebFetch lets JARVIS look things up (and read the page,
    # not just snippets) even without an API key — non-interactive mode can't prompt
    # for tool permission, so both must be granted upfront.
    convo = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    full_prompt = f"{convo}\n\nASSISTANT:"
    result = subprocess.run(
        ["claude", "-p", full_prompt, "--system-prompt", system_prompt, "--allowedTools", "WebSearch,WebFetch"],
        capture_output=True, text=True, timeout=90,
        stdin=subprocess.DEVNULL,  # daemonized server has no real stdin; without this,
        # `claude -p` waits several seconds probing for piped input before proceeding.
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr or "claude CLI returned no output")
    return result.stdout.strip()


def call_model(cfg, system_prompt, messages, fallback_text):
    if cfg.has_real_api_key():
        try:
            return call_anthropic(cfg, system_prompt, messages)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
            print(f"[jarvis] Anthropic API call failed ({e}); trying claude CLI…", file=sys.stderr)
    try:
        return call_claude_cli(system_prompt, messages)
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError, OSError) as e:
        print(f"[jarvis] claude CLI fallback failed ({e})", file=sys.stderr)
    return fallback_text
