#!/usr/bin/env python3
"""tools/novatek_autopilot.py — standalone, zero-AI Novatek form runner (CLI).

Drives the deterministic form autopilot that lives inside the browser daemon
(tools/browser_daemon.py) entirely from the command line: no LLM, no tokens,
no model latency — just the rule engine (first option / 'test' / '55' /
today's date / current time / consent PDF / Admin signature) executed at
machine speed over localhost HTTP.

Usage:
    python3 tools/novatek_autopilot.py status
    python3 tools/novatek_autopilot.py open [--url URL] [--profile NAME]
    python3 tools/novatek_autopilot.py form     # fill+submit the open form
    python3 tools/novatek_autopilot.py visit    # fill+submit every form of the visit
    python3 tools/novatek_autopilot.py takeover [--visits N]   # whole participant

Common flags: --json (raw report), --tab N, --daemon-url URL, --no-launch.
Exit code 0 means the requested flow completed verified; 1 means it did not
(the report says exactly what is left); 2 means the daemon was unreachable.

The daemon is auto-started when not already running (honouring
BROWSER_DAEMON_PORT / BROWSER_DAEMON_HEADLESS / BROWSER_DAEMON_PROFILE_BASE,
which is how the offline test suite runs this whole stack headless against a
mock Novatek page).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON_SCRIPT = os.path.join(ROOT, "tools", "browser_daemon.py")
VENV_PYTHON = os.path.join(ROOT, ".venv-browser", "bin", "python")
DAEMON_LOG = os.path.join(ROOT, "tools", "browser_daemon.log")

DEFAULT_URL = "https://nec-dev.autotrial.app"
DEFAULT_USERNAME = "Admin"
DEFAULT_PASSWORD = "nursenurse123"
DEFAULT_CONSENT_PDF = os.path.join(ROOT, "Informed_Consent Template.pdf")

EXIT_OK, EXIT_INCOMPLETE, EXIT_NO_DAEMON = 0, 1, 2


def default_daemon_url():
    return f"http://127.0.0.1:{os.environ.get('BROWSER_DAEMON_PORT', '4701')}"


class Daemon:
    """Thin HTTP client for the browser daemon."""

    def __init__(self, base_url, auto_launch=True):
        self.base_url = base_url.rstrip("/")
        self.auto_launch = auto_launch

    def request(self, path, payload=None, timeout=65):
        data = None if path == "/health" else json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method="GET" if path == "/health" else "POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"error": f"daemon HTTP {e.code}"}

    def alive(self):
        try:
            return bool(self.request("/health", timeout=2).get("ok"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False

    def ensure(self):
        """Returns None when the daemon is reachable, else an error string."""
        if self.alive():
            return None
        if not self.auto_launch:
            return f"browser daemon is not running at {self.base_url} (and --no-launch was given)"
        if not os.path.exists(VENV_PYTHON):
            return "browser environment not installed — run tools/setup_browser.sh once"
        with open(DAEMON_LOG, "a") as log:
            subprocess.Popen(
                [VENV_PYTHON, DAEMON_SCRIPT],
                stdout=log, stderr=log,
                start_new_session=True,
                env=os.environ.copy(),
            )
        for _ in range(40):
            time.sleep(0.3)
            if self.alive():
                return None
        return f"browser daemon failed to start — see {DAEMON_LOG}"


# ----------------------------------------------------------------------------
# Widget helpers (mirror the daemon's conventions)
# ----------------------------------------------------------------------------

def _widgets(daemon, tab_index=None):
    res = daemon.request("/flutter_widgets", {"tab_index": tab_index, "verbose": True},
                         timeout=30)
    return res.get("widgets") or res.get("elements") or []


def _find(widgets, role=None, label_contains=None, label_exact=None):
    for w in widgets:
        lbl = (w.get("label") or "").strip()
        if role and w.get("role") != role:
            continue
        if label_exact is not None and lbl.lower() != label_exact.lower():
            continue
        if label_contains is not None and label_contains.lower() not in lbl.lower():
            continue
        return w
    return None


def _screen_of(widgets):
    labels = [(w.get("label") or "").strip() for w in widgets]
    if any(l == "Visit Mode" for l in labels):
        return "visit_mode"
    if any("Visit Progress" in l or "Participant profile" in l for l in labels):
        return "participant_profile"
    lowered = [l.lower() for l in labels]
    if any("password" in l for l in lowered):
        return "login"
    return "unknown"


# ----------------------------------------------------------------------------
# Subcommands
# ----------------------------------------------------------------------------

def cmd_status(daemon, args):
    widgets = _widgets(daemon, args.tab)
    tabs = daemon.request("/list", {}, timeout=15)
    report = {
        "daemon": daemon.base_url,
        "screen": _screen_of(widgets),
        "tabs": tabs.get("tabs", tabs.get("total_tabs")),
        "widgets_seen": len(widgets),
    }
    for w in widgets:
        lbl = (w.get("label") or "").strip().replace(" ", "")
        if lbl and "/" in lbl and lbl.replace("/", "").isdigit():
            report["forms_progress"] = lbl
            break
    return report, EXIT_OK


def cmd_open(daemon, args):
    report = {"url": args.url, "steps": []}
    res = daemon.request("/open", {"url": args.url, "profile": args.profile,
                                   "tab_index": args.tab}, timeout=90)
    if res.get("error"):
        return {"error": f"open failed: {res['error']}"}, EXIT_INCOMPLETE
    report["steps"].append("opened")
    time.sleep(2.5)

    detect = daemon.request("/detect_app_type", {"tab_index": args.tab}, timeout=30)
    report["app_type"] = detect.get("app_type")
    report["steps"].append(f"detected {detect.get('app_type')}")

    # Deterministic login: the login screen has username/password textboxes.
    for attempt in range(3):
        widgets = _widgets(daemon, args.tab)
        screen = _screen_of(widgets)
        if screen != "login":
            report["screen"] = screen
            report["steps"].append(f"on {screen} screen")
            return report, EXIT_OK
        user_box = (_find(widgets, role="textbox", label_contains="user")
                    or _find(widgets, role="textbox", label_contains="email"))
        pass_box = _find(widgets, role="textbox", label_contains="password")
        if user_box:
            daemon.request("/flutter_type", {
                "target": user_box.get("selector_hint") or user_box.get("label"),
                "text": args.username, "tab_index": args.tab}, timeout=30)
        if pass_box:
            daemon.request("/flutter_type", {
                "target": pass_box.get("selector_hint") or pass_box.get("label"),
                "text": args.password, "tab_index": args.tab}, timeout=30)
        widgets = _widgets(daemon, args.tab)
        login_btn = (_find(widgets, role="button", label_exact="Login")
                     or _find(widgets, role="button", label_contains="log in")
                     or _find(widgets, role="button", label_contains="sign in")
                     or _find(widgets, role="button", label_contains="login"))
        if login_btn:
            daemon.request("/flutter_click", {
                "target": login_btn.get("selector_hint") or login_btn.get("label"),
                "tab_index": args.tab}, timeout=30)
            report["steps"].append("submitted credentials")
        time.sleep(2.5)

    widgets = _widgets(daemon, args.tab)
    report["screen"] = _screen_of(widgets)
    ok = report["screen"] != "login"
    if not ok:
        report["error"] = "still on the login screen after 3 attempts"
    return report, (EXIT_OK if ok else EXIT_INCOMPLETE)


def _fill_payload(args):
    payload = {"tab_index": args.tab}
    for k, opt in (("text_value", "text"), ("number_value", "number"),
                   ("file_path", "file"), ("username", "username"),
                   ("password", "password")):
        v = getattr(args, opt, None)
        if v:
            payload[k] = v
    return payload


def cmd_form(daemon, args):
    report = daemon.request("/autofill_form", _fill_payload(args), timeout=420)
    report.pop("widgets", None)
    ok = bool(report.get("submit_verified")) and not report.get("unresolved")
    return report, (EXIT_OK if ok else EXIT_INCOMPLETE)


def cmd_visit(daemon, args):
    report = daemon.request("/autofill_visit", _fill_payload(args), timeout=580)
    report.pop("widgets", None)
    ok = bool(report.get("all_forms_submitted"))
    return report, (EXIT_OK if ok else EXIT_INCOMPLETE)


def cmd_takeover(daemon, args):
    payload = _fill_payload(args)
    payload["max_visits"] = args.visits
    report = daemon.request("/takeover_participant", payload, timeout=3300)
    report.pop("widgets", None)
    ok = report.get("status") in ("takeover_done", "no_next_visit")
    return report, (EXIT_OK if ok else EXIT_INCOMPLETE)


# ----------------------------------------------------------------------------
# Report rendering
# ----------------------------------------------------------------------------

def _print_human(report, code):
    verdict = "COMPLETE" if code == EXIT_OK else "INCOMPLETE"
    print(f"== Novatek autopilot: {verdict} ==")
    for key in ("status", "screen", "app_type", "forms_progress",
                "progress_before", "progress_after", "progress",
                "submitted", "submit_verified", "all_forms_submitted",
                "visits_completed", "rounds", "error"):
        if key in report and report[key] is not None:
            print(f"  {key}: {report[key]}")
    for step in report.get("steps", []):
        print(f"  step: {step}")
    answered = report.get("answered")
    if answered is not None:
        print(f"  questions answered: {len(answered)}")
    for form in report.get("forms", []):
        if "form" in form:
            print(f"  form {form.get('form')}: {form.get('status')} "
                  f"(answered {form.get('answered')}, progress {form.get('progress_after')})")
        for item in form.get("unresolved") or []:
            print(f"    unresolved: {item}")
        if form.get("note") or form.get("error"):
            print(f"    note: {form.get('note') or form.get('error')}")
    for visit in report.get("visits", []):
        print(f"  visit {visit.get('visit')}: ended={visit.get('ended')} "
              f"progress={visit.get('progress')}"
              + (f" error={visit['error']}" if visit.get("error") else ""))
    for item in report.get("unresolved") or []:
        print(f"  unresolved: {item}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deterministic Novatek form autopilot — zero AI, pure rule engine.")
    parser.add_argument("--daemon-url", default=default_daemon_url())
    parser.add_argument("--json", action="store_true", help="print the raw JSON report")
    parser.add_argument("--tab", type=int, default=None, help="tab index to act on")
    parser.add_argument("--no-launch", action="store_true",
                        help="fail instead of auto-starting the daemon")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="daemon health + current screen")

    p_open = sub.add_parser("open", help="open Novatek and log in deterministically")
    p_open.add_argument("--url", default=DEFAULT_URL)
    p_open.add_argument("--profile", default=None)
    p_open.add_argument("--username", default=DEFAULT_USERNAME)
    p_open.add_argument("--password", default=DEFAULT_PASSWORD)

    for name, help_text in (("form", "fill+submit the currently open form (Visit Mode)"),
                            ("visit", "fill+submit every form of the current visit"),
                            ("takeover", "complete visit after visit for the participant")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--text", default=None, help="text answer (default 'test')")
        p.add_argument("--number", default=None, help="number answer (default '55')")
        p.add_argument("--file", default=None,
                       help=f"upload file (default {DEFAULT_CONSENT_PDF})")
        p.add_argument("--username", default=None, help="signature username")
        p.add_argument("--password", default=None, help="signature password")
        if name == "takeover":
            p.add_argument("--visits", type=int, default=5, help="max visits (default 5)")

    args = parser.parse_args(argv)

    daemon = Daemon(args.daemon_url, auto_launch=not args.no_launch)
    err = daemon.ensure()
    if err:
        print(json.dumps({"error": err}) if args.json else f"ERROR: {err}", file=sys.stderr)
        return EXIT_NO_DAEMON

    handler = {"status": cmd_status, "open": cmd_open, "form": cmd_form,
               "visit": cmd_visit, "takeover": cmd_takeover}[args.command]
    try:
        report, code = handler(daemon, args)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # A read timeout means the daemon is alive and still working the form
        # — that is "incomplete", not "unreachable". Only a dead connection
        # earns exit code 2.
        reason = getattr(e, "reason", e)
        if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
            msg = ("timed out waiting for the daemon — it may still be working; "
                   "check again with 'status'")
            print(json.dumps({"error": msg}) if args.json else f"ERROR: {msg}",
                  file=sys.stderr)
            return EXIT_INCOMPLETE
        print(json.dumps({"error": str(e)}) if args.json else f"ERROR: {e}", file=sys.stderr)
        return EXIT_NO_DAEMON

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
