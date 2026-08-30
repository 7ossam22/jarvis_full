#!/usr/bin/env python3
"""End-to-end tests for the deterministic Novatek autopilot — fully offline.

Spins up the real browser daemon (headless Chromium, isolated port and
profile dir) plus a local web server hosting mock_novatek.html — a faithful
stand-in for the Novatek Flutter app's semantics tree — then drives it with
tools/novatek_autopilot.py exactly the way production does, asserting the
verification gates (progress counter, submit_verified, all_forms_submitted,
visits_completed) actually pass. No AI, no network, no real Novatek.

Run:  python3 tools/tests/test_e2e_mock.py -v
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(ROOT, ".venv-browser", "bin", "python")
DAEMON = os.path.join(ROOT, "tools", "browser_daemon.py")
CLI = os.path.join(ROOT, "tools", "novatek_autopilot.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web_port = free_port()
        cls.daemon_port = free_port()
        cls.profile_dir = tempfile.mkdtemp(prefix="autopilot-e2e-profile-")
        cls.daemon_log = os.path.join(tempfile.gettempdir(),
                                      f"autopilot-e2e-daemon-{cls.daemon_port}.log")

        cls.web = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(cls.web_port),
             "--bind", "127.0.0.1", "--directory", TESTS_DIR],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cls.env = os.environ.copy()
        cls.env.update({
            "BROWSER_DAEMON_PORT": str(cls.daemon_port),
            "BROWSER_DAEMON_HEADLESS": "1",
            "BROWSER_DAEMON_PROFILE_BASE": cls.profile_dir,
        })
        cls.daemon_log_fh = open(cls.daemon_log, "w")
        cls.daemon = subprocess.Popen(
            [VENV_PYTHON, DAEMON],
            stdout=cls.daemon_log_fh, stderr=cls.daemon_log_fh, env=cls.env)

        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{cls.daemon_port}/health", timeout=2) as r:
                    if json.loads(r.read()).get("ok"):
                        break
            except OSError:
                time.sleep(0.3)
        else:
            cls.tearDownClass()
            raise RuntimeError(f"daemon did not come up — see {cls.daemon_log}")

    @classmethod
    def tearDownClass(cls):
        for proc in (getattr(cls, "daemon", None), getattr(cls, "web", None)):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        fh = getattr(cls, "daemon_log_fh", None)
        if fh:
            fh.close()
        shutil.rmtree(cls.profile_dir, ignore_errors=True)

    # ------------------------------------------------------------------ utils

    def cli(self, *args, timeout=600):
        proc = subprocess.run(
            [sys.executable, CLI, "--json", "--no-launch", *args],
            capture_output=True, text=True, timeout=timeout, env=self.env)
        out = proc.stdout.strip() or proc.stderr.strip()
        try:
            report = json.loads(out)
        except ValueError:
            report = {"raw": out}
        return proc.returncode, report

    def mock_url(self, **params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"http://127.0.0.1:{self.web_port}/mock_novatek.html" + (f"?{qs}" if qs else "")

    def navigate(self, **params):
        code, report = self.cli("open", "--url", self.mock_url(**params))
        self.assertEqual(code, 0, f"open failed: {report}")
        return report

    # ------------------------------------------------------------------ tests

    def test_01_status_sees_visit_mode(self):
        self.navigate(forms=1, visits=1)
        code, report = self.cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(report.get("screen"), "visit_mode")
        self.assertEqual(report.get("forms_progress"), "0/1")

    def test_02_form_fills_and_submits_long_form(self):
        """The 10-question long form: radio, text, number, live-validated
        number (error-retry path), date, time, checkboxes, unit dropdown,
        file upload, and the signature dialog — plus the scroll loop and the
        pre-submit sweep."""
        self.navigate(forms=1, visits=1)
        code, report = self.cli("form")
        self.assertEqual(code, 0, f"form run failed: {json.dumps(report, indent=2)[:2000]}")
        self.assertTrue(report.get("submitted"))
        self.assertTrue(report.get("submit_verified"))
        self.assertEqual(report.get("unresolved"), [])
        self.assertEqual(report.get("progress_before"), "0/1")
        self.assertEqual(report.get("progress_after"), "1/1")
        # Every one of the 10 questions must have been acted on.
        answered_keys = {a["question"].split(" ")[0] for a in report.get("answered", [])}
        for n in range(1, 11):
            self.assertIn(f"{n}.", answered_keys,
                          f"question {n} never acted on: {report.get('answered')}")

    def test_03_visit_fills_both_forms(self):
        self.navigate(forms=2, visits=1)
        code, report = self.cli("visit")
        self.assertEqual(code, 0, f"visit run failed: {json.dumps(report, indent=2)[:2000]}")
        self.assertTrue(report.get("all_forms_submitted"))
        self.assertEqual(report.get("progress"), "2/2")
        forms = [f for f in report.get("forms", []) if "form" in f]
        self.assertEqual(len(forms), 2)
        for f in forms:
            self.assertEqual(f.get("status"), "autofill_done")
            self.assertEqual(f.get("unresolved"), [])

    def test_04_takeover_completes_all_visits_and_stops(self):
        """From the participant profile: start visit, fill all forms, enter
        the Actual visit date, End Visit, return to profile — twice — then
        stop cleanly when no next visit remains."""
        self.navigate(view="profile", visits=2, forms=2)
        code, report = self.cli("takeover", "--visits", "4")
        self.assertEqual(code, 0, f"takeover failed: {json.dumps(report, indent=2)[:2000]}")
        self.assertEqual(report.get("status"), "no_next_visit")
        self.assertEqual(report.get("visits_completed"), 2)
        ended = [v for v in report.get("visits", []) if v.get("ended")]
        self.assertEqual(len(ended), 2)
        self.assertEqual(ended[0].get("visit"), "Screening Visit")
        self.assertEqual(ended[1].get("visit"), "Week 1 Visit")
        for v in ended:
            self.assertEqual(v.get("progress"), "2/2")
            self.assertIsNotNone(v.get("actual_date"))

    def test_05_form_refuses_off_visit_screen(self):
        """The hard scope guard: the autopilot must refuse to run anywhere
        but the Visit Mode screen."""
        self.navigate(view="profile", visits=1, forms=1)
        code, report = self.cli("form")
        self.assertEqual(code, 1)
        self.assertIn("Visit Mode", report.get("error", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
