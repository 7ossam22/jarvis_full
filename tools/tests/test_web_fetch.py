#!/usr/bin/env python3
"""tools/tests/test_web_fetch.py — tests for the web_fetch tool.

    python3 tools/tests/test_web_fetch.py

Layers:
  SecurityTests    the SSRF guards — the reason this module exists. Every one
                   of these is a URL a model could plausibly be talked into
                   fetching from a server bound to 0.0.0.0 with no auth.
  ExtractorTests   HTML -> readable text.
  BehaviourTests   argument handling, content-type gating, truncation, and the
                   guarantee that no failure escapes as an exception.
  RedirectTests    a real local HTTP server that 302s to a private address,
                   proving the redirect hop is re-validated and not followed.
  LiveFetchTests   one real page. Skipped when offline.

Standard library only.
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.connectors import web_fetch as wf                     # noqa: E402
from app.connectors.registry import ANTHROPIC, LMSTUDIO, registry  # noqa: E402


class SecurityTests(unittest.TestCase):
    """Each case is a real SSRF vector against this specific app."""

    BLOCKED = [
        ("file:///etc/passwd", "urllib installs a FileHandler by default"),
        ("ftp://example.com/x", "non-http scheme"),
        ("gopher://example.com/", "non-http scheme"),
        ("data:text/html,<h1>x</h1>", "non-http scheme"),
        ("http://127.0.0.1:4701/list", "the browser-automation daemon"),
        ("http://localhost:4700/config", "JARVIS's own server"),
        ("http://169.254.169.254/latest/meta-data/", "cloud metadata endpoint"),
        ("http://192.168.1.121:1234/v1/models", "the LM Studio box on the LAN"),
        ("http://10.0.0.5/", "RFC1918"),
        ("http://172.16.4.4/", "RFC1918"),
        ("http://[::1]:4700/", "IPv6 loopback"),
        ("http://0.0.0.0/", "unspecified"),
        ("/etc/passwd", "no scheme at all"),
    ]

    def test_dangerous_urls_are_refused(self):
        for url, why in self.BLOCKED:
            with self.subTest(url=url, why=why):
                out = wf.execute_web_fetch({"url": url})
                self.assertEqual(out["status"], "error", f"{url} was NOT blocked ({why})")
                self.assertIn("Refusing to fetch", out["error"])

    def test_public_addresses_pass_the_guard(self):
        # Guard only — no request is made here.
        for url in ("https://example.com/x", "http://93.184.216.34/"):
            with self.subTest(url):
                self.assertEqual(wf._assert_fetchable(url), url)

    def test_ip_classification(self):
        for addr, public in [("8.8.8.8", True), ("1.1.1.1", True), ("127.0.0.1", False),
                             ("10.1.2.3", False), ("192.168.0.1", False), ("172.31.255.1", False),
                             ("169.254.169.254", False), ("::1", False), ("224.0.0.1", False)]:
            with self.subTest(addr):
                self.assertEqual(wf._is_public_ip(addr), public)

    def test_hostname_resolving_to_private_space_is_blocked(self):
        # The literal-IP check is easy to pass; the DNS path is the subtle one.
        real = wf.socket.getaddrinfo
        wf.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 80))]
        try:
            out = wf.execute_web_fetch({"url": "http://sneaky.example.com/"})
            self.assertEqual(out["status"], "error")
            self.assertIn("127.0.0.1", out["error"])
        finally:
            wf.socket.getaddrinfo = real

    def test_mixed_resolution_is_blocked(self):
        # A name resolving to one public and one private address must fail:
        # otherwise the private one is reachable.
        real = wf.socket.getaddrinfo
        wf.socket.getaddrinfo = lambda *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("10.0.0.9", 80))]
        try:
            self.assertEqual(wf.execute_web_fetch({"url": "http://mixed.example/"})["status"], "error")
        finally:
            wf.socket.getaddrinfo = real


class ExtractorTests(unittest.TestCase):
    def extract(self, html):
        p = wf.HTMLTextExtractor()
        p.feed(html)
        p.close()
        return p

    def test_title_and_body_text(self):
        p = self.extract("<html><head><title> Cairo Weather </title></head>"
                         "<body><p>It is 34&deg;C today.</p></body></html>")
        self.assertEqual(p.title.strip(), "Cairo Weather")
        self.assertEqual(p.text, "It is 34°C today.")

    def test_chrome_is_dropped(self):
        p = self.extract("<body><script>var x=1;</script><style>a{}</style>"
                         "<nav>Home About</nav><p>Real content.</p>"
                         "<footer>(c) 2026</footer></body>")
        self.assertEqual(p.text, "Real content.")

    def test_block_tags_become_line_breaks(self):
        p = self.extract("<body><p>One</p><p>Two</p><br/><div>Three</div></body>")
        self.assertEqual([l for l in p.text.split("\n") if l], ["One", "Two", "Three"])

    def test_whitespace_collapses_but_paragraphs_survive(self):
        p = self.extract("<body><p>a     b\t\tc</p><p>d</p></body>")
        self.assertIn("a b c", p.text)
        self.assertIn("\n", p.text)

    def test_self_closing_skip_tag_does_not_wedge_state(self):
        p = self.extract("<body><p>before</p><br/><p>after</p></body>")
        self.assertIn("before", p.text)
        self.assertIn("after", p.text)


class BehaviourTests(unittest.TestCase):
    def setUp(self):
        self._real = wf._fetch
        self.addCleanup(lambda: setattr(wf, "_fetch", self._real))

    def serve(self, body, content_type="text/html", url="https://example.com/p"):
        wf._fetch = lambda _u: (url, content_type, body)

    def test_bad_arguments(self):
        for bad in ({}, {"url": "  "}, {"url": None}, "not-a-dict"):
            with self.subTest(repr(bad)):
                self.assertEqual(wf.execute_web_fetch(bad)["status"], "error")

    def test_non_text_content_type_reported_not_decoded(self):
        self.serve(b"\x89PNG", content_type="image/png")
        out = wf.execute_web_fetch({"url": "https://example.com/a.png"})
        self.assertEqual(out["status"], "error")
        self.assertIn("image/png", out["error"])

    def test_plain_text_passes_through_unparsed(self):
        self.serve("just text", content_type="text/plain")
        self.assertEqual(wf.execute_web_fetch({"url": "https://example.com/t"})["content"], "just text")

    def test_truncation_and_max_chars_clamping(self):
        self.serve("<p>" + ("x" * 50000) + "</p>")
        out = wf.execute_web_fetch({"url": "https://example.com/l"})
        self.assertTrue(out["truncated"])
        self.assertEqual(out["chars"], wf.DEFAULT_MAX_CHARS)

        out = wf.execute_web_fetch({"url": "https://example.com/l", "max_chars": 999999})
        self.assertEqual(out["chars"], wf.HARD_MAX_CHARS)      # clamped, not honoured

        out = wf.execute_web_fetch({"url": "https://example.com/l", "max_chars": "garbage"})
        self.assertEqual(out["chars"], wf.DEFAULT_MAX_CHARS)   # falls back, does not crash

    def test_empty_page_is_success_with_a_note(self):
        self.serve("<body><script>everything()</script></body>")
        out = wf.execute_web_fetch({"url": "https://example.com/js"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["chars"], 0)
        self.assertIn("note", out)

    def test_every_failure_becomes_a_structured_error(self):
        for label, exc in [
            ("404", urllib.error.HTTPError("u", 404, "Not Found", {}, None)),
            ("offline", urllib.error.URLError("no route to host")),
            ("timeout", TimeoutError("timed out")),
            ("unforeseen", RuntimeError("kaboom")),
        ]:
            with self.subTest(label):
                def boom(_u, e=exc):
                    raise e
                wf._fetch = boom
                out = wf.execute_web_fetch({"url": "https://example.com/x"})
                self.assertEqual(out["status"], "error")
                self.assertIsInstance(out["error"], str)


class _RedirectToPrivateHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:4701/list")
        self.end_headers()


class RedirectTests(unittest.TestCase):
    """A public page that redirects into private space must not be followed."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectToPrivateHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_redirect_into_private_space_is_refused(self):
        # Bypass the entry guard (the origin is itself localhost here) so the
        # redirect handler is what has to catch it.
        real = wf._assert_fetchable
        seen = []

        def only_first(url):
            seen.append(url)
            return url if len(seen) == 1 else real(url)

        wf._assert_fetchable = only_first
        try:
            out = wf.execute_web_fetch({"url": f"http://127.0.0.1:{self.port}/start"})
        finally:
            wf._assert_fetchable = real
        self.assertEqual(out["status"], "error")
        self.assertIn("127.0.0.1", out["error"])
        self.assertEqual(len(seen), 2, "the redirect hop was not re-validated")


class RegistryTests(unittest.TestCase):
    def test_registered_for_every_provider(self):
        for p in (ANTHROPIC, LMSTUDIO):
            names = {t.get("name") or t["function"]["name"]
                     for t in registry.get_tools_for_provider(p)}
            self.assertIn("web_fetch", names)

    def test_dispatch_shields_and_blocks(self):
        out = registry.dispatch("web_fetch", {"url": "file:///etc/passwd"}, ANTHROPIC, None)
        self.assertEqual(out["status"], "error")


class LiveFetchTests(unittest.TestCase):
    def test_real_page(self):
        out = wf.execute_web_fetch({"url": "https://example.com/"})
        if out["status"] == "error":
            self.skipTest(f"network unavailable: {out['error']}")
        self.assertIn("Example Domain", out["content"])
        print(f"\n    live fetch: {out['title']!r} -> {out['chars']} chars")


if __name__ == "__main__":
    unittest.main(verbosity=2)
