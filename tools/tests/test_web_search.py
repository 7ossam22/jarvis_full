#!/usr/bin/env python3
"""tools/tests/test_web_search.py — tests for the web_search tool and its
registration, runnable with no API key and (mostly) no network.

    python3 tools/tests/test_web_search.py          # all layers
    python3 tools/tests/test_web_search.py -k Live  # only the live-network one

Four layers, cheapest first:

  ParserTests        html.parser against captured DuckDuckGo Lite markup.
  FailureTests       every network/parse failure path returns data, not an
                     exception — the property the server thread depends on.
  RegistryTests      the tool is registered, visible to every provider, in the
                     right wire shape, with no duplicate names.
  EndToEndTests      the REAL provider tool loop, driven by a stub LM Studio
                     server on localhost. No credentials needed: the stub
                     answers with a web_search tool_call, the provider runs the
                     genuine search, feeds the genuine result back, and the stub
                     returns a final answer. This is what proves the wiring,
                     not just the parts.

  LiveSearchTests    one real request to DuckDuckGo. Skipped automatically when
                     the network is unavailable, so the suite stays green
                     offline.

Standard library only, same as the app.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app import controllers                                    # noqa: E402
from app.config import Config                                  # noqa: E402
from app.connectors import web_search as ws                    # noqa: E402
from app.connectors.registry import (                          # noqa: E402
    ANTHROPIC, GEMINI, LMSTUDIO, OPENAI, registry,
)

# A captured Lite result page: redirect-wrapped href, HTML entities, a numeric
# charref, and messy whitespace — every quirk the parser has to survive.
SAMPLE_HTML = """<html><body><table>
<tr><td>1.&nbsp;</td><td><a rel="nofollow" class='result-link'
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FCairo&amp;rut=ab12"
    >Cairo - Wikipedia</a></td></tr>
<tr><td>&nbsp;</td><td class='result-snippet'>Cairo is the capital of Egypt &amp; the
    largest city in the Arab world.</td></tr>
<tr><td>2.&nbsp;</td><td><a class="result-link" href="https://weather.com/cairo">Cairo Weather</a></td></tr>
<tr><td>&nbsp;</td><td class="result-snippet">Currently   32&#176;C   and    sunny.</td></tr>
</table></body></html>"""


class ParserTests(unittest.TestCase):
    def results(self, html):
        p = ws.DuckDuckGoLiteParser()
        p.feed(html)
        p.close()
        return p.results

    def test_extracts_title_url_and_snippet(self):
        r = self.results(SAMPLE_HTML)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["title"], "Cairo - Wikipedia")
        self.assertEqual(r[1]["url"], "https://weather.com/cairo")

    def test_unwraps_duckduckgo_redirect(self):
        # A raw //duckduckgo.com/l/?uddg=... href is useless to the model, which
        # persona.py asks to emit a SOURCE: line.
        self.assertEqual(self.results(SAMPLE_HTML)[0]["url"], "https://en.wikipedia.org/wiki/Cairo")

    def test_decodes_entities_and_collapses_whitespace(self):
        r = self.results(SAMPLE_HTML)
        self.assertIn("Egypt & the largest", r[0]["snippet"])
        self.assertEqual(r[1]["snippet"], "Currently 32°C and sunny.")

    def test_class_token_matching_survives_extra_classes(self):
        html = "<td class='result-snippet extra-class'>hello</td>"
        self.assertEqual(self.results(html)[0]["snippet"], "hello")

    def test_snippet_without_a_link_is_kept_not_dropped(self):
        self.assertEqual(self.results("<td class='result-snippet'>orphan</td>")[0]["snippet"], "orphan")

    def test_unknown_markup_yields_nothing_rather_than_raising(self):
        self.assertEqual(self.results("<html><p>no results here</p></html>"), [])

    def test_redirect_helper_edge_cases(self):
        self.assertEqual(ws.unwrap_redirect(""), "")
        self.assertEqual(ws.unwrap_redirect("//duckduckgo.com/x"), "https://duckduckgo.com/x")
        self.assertEqual(ws.unwrap_redirect("https://a.example/b?c=d"), "https://a.example/b?c=d")


class FailureTests(unittest.TestCase):
    """execute_web_search must never raise: it runs on the worker thread that
    is serving one HTTP request, where an escaping exception ends the turn."""

    def setUp(self):
        self._real_urlopen = urllib.request.urlopen
        self._real_fetch = ws._fetch
        self.addCleanup(lambda: setattr(urllib.request, "urlopen", self._real_urlopen))
        self.addCleanup(lambda: setattr(ws, "_fetch", self._real_fetch))

    def raising_urlopen(self, exc):
        def _f(*_a, **_k):
            raise exc
        urllib.request.urlopen = _f

    def test_every_network_failure_becomes_a_structured_error(self):
        for label, exc in [
            ("http 429", urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)),
            ("http 403", urllib.error.HTTPError("u", 403, "Forbidden", {}, None)),
            ("dns/offline", urllib.error.URLError("nodename nor servname provided")),
            ("timeout", TimeoutError("timed out")),
            ("socket", OSError("connection reset")),
            ("unforeseen", RuntimeError("kaboom")),
        ]:
            with self.subTest(label):
                self.raising_urlopen(exc)
                out = ws.execute_web_search({"query": "x"})
                self.assertEqual(out["status"], "error")
                self.assertIsInstance(out["error"], str)

    def test_http_error_reports_the_status_code(self):
        self.raising_urlopen(urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None))
        self.assertIn("429", ws.execute_web_search({"query": "x"})["error"])

    def test_malformed_arguments_rejected(self):
        for bad in ({}, {"query": "   "}, {"query": None}, "not-a-dict"):
            with self.subTest(repr(bad)):
                self.assertEqual(ws.execute_web_search(bad)["status"], "error")

    def test_no_results_is_success_not_error(self):
        # The request worked; the page just held nothing. The model should say
        # so rather than treat it as a fault and retry.
        ws._fetch = lambda _q: "<html><body>nothing</body></html>"
        out = ws.execute_web_search({"query": "zxqw"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 0)
        self.assertIn("note", out)

    def test_results_capped_for_context_budget(self):
        rows = "".join(f"<td class='result-snippet'>snippet {i}</td>" for i in range(30))
        ws._fetch = lambda _q: f"<table>{rows}</table>"
        self.assertEqual(ws.execute_web_search({"query": "many"})["count"], ws.MAX_RESULTS)


class RegistryTests(unittest.TestCase):
    @staticmethod
    def offered(provider):
        return {t.get("name") or t["function"]["name"]
                for t in registry.get_tools_for_provider(provider)}

    def test_offered_only_to_backends_without_search_of_their_own(self):
        # Anthropic and Gemini each run a real search index server-side
        # (`web_search`, `google_search`), declared in their own provider
        # modules and better than scraping DuckDuckGo. This tool exists for
        # backends that have neither — see the registration in registry.py.
        self.assertIn("web_search", self.offered(LMSTUDIO))
        for p in (ANTHROPIC, GEMINI):
            with self.subTest(p):
                self.assertNotIn("web_search", self.offered(p))

    def test_gated_as_local_only(self):
        tool = registry.get("web_search")
        self.assertTrue(tool.only_llm)
        self.assertTrue(tool.is_allowed_for(LMSTUDIO))
        for p in (ANTHROPIC, GEMINI, OPENAI):
            with self.subTest(p):
                self.assertFalse(tool.is_allowed_for(p))

    def test_dispatch_refuses_a_hosted_provider(self):
        out = registry.dispatch("web_search", {"query": "x"}, ANTHROPIC, None)
        self.assertEqual(out["status"], "error")
        self.assertIn("restricted", out["error"])

    def test_no_clash_with_anthropics_own_web_search(self):
        # anthropic_provider.py injects {"name": "web_search"} of its own; the
        # registry must not also offer one, or the request is rejected.
        from app.providers import anthropic_provider as ap
        self.assertNotIn("web_search", self.offered(ANTHROPIC))
        import inspect
        self.assertIn("web_search_20260209", inspect.getsource(ap.call_anthropic))

    def test_no_duplicate_tool_names_in_any_payload(self):
        # Regression: the Anthropic provider used to declare its own server-side
        # tool literally named "web_search"; two of that name in one request is
        # an API validation error.
        for p in (ANTHROPIC, GEMINI, LMSTUDIO):
            with self.subTest(p):
                names = [t.get("name") or t["function"]["name"]
                         for t in registry.get_tools_for_provider(p)]
                self.assertEqual(len(names), len(set(names)))

    def test_schema_shape_per_provider(self):
        # web_search is local-only now, so shape-check it on LM Studio and use
        # web_fetch (offered everywhere) for the hosted wire formats.
        o = next(t for t in registry.get_tools_for_provider(LMSTUDIO)
                 if t["function"]["name"] == "web_search")
        self.assertEqual(o["type"], "function")
        self.assertEqual(o["function"]["parameters"]["required"], ["query"])
        a = next(t for t in registry.get_tools_for_provider(ANTHROPIC) if t["name"] == "web_fetch")
        g = next(t for t in registry.get_tools_for_provider(GEMINI) if t["name"] == "web_fetch")
        self.assertEqual(set(a), {"name", "description", "input_schema"})
        self.assertEqual(set(g), {"name", "description", "parameters"})

    def test_dispatch_shields_a_raising_handler(self):
        real = registry.get("web_search").handler
        try:
            object.__setattr__(registry.get("web_search"), "handler",
                               lambda _a: (_ for _ in ()).throw(RuntimeError("boom")))
            out = registry.dispatch("web_search", {"query": "x"}, LMSTUDIO, None)
            self.assertEqual(out, {"status": "error", "error": "boom"})
        finally:
            object.__setattr__(registry.get("web_search"), "handler", real)


class _StubLMStudioHandler(BaseHTTPRequestHandler):
    """An OpenAI-compatible /v1/chat/completions that asks for one web_search
    call, then answers. Stands in for a real local model."""

    received: list[dict] = []

    def log_message(self, *_a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _StubLMStudioHandler.received.append(body)
        if len(_StubLMStudioHandler.received) == 1:
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "web_search",
                             "arguments": json.dumps({"query": "capital of Egypt"})},
            }]}
        else:
            message = {"role": "assistant",
                       "content": "Cairo, sir.\nSOURCE: https://en.wikipedia.org/wiki/Cairo"}
        payload = json.dumps({"choices": [{"message": message}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class EndToEndTests(unittest.TestCase):
    """Drives the real LMStudioProvider loop and the real controller against a
    stub model, with the search itself stubbed so the test is deterministic.
    Exercises: controllers.handle_chat -> call_model -> provider tool loop ->
    registry.get_tools_for_provider -> registry.dispatch -> answer parsing."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubLMStudioHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _StubLMStudioHandler.received.clear()
        self._real_fetch = ws._fetch
        ws._fetch = lambda _q: SAMPLE_HTML          # deterministic, no network
        self.addCleanup(lambda: setattr(ws, "_fetch", self._real_fetch))
        self.cfg = Config({"model": {
            "provider": "lmstudio",
            "lmstudio_base_url": f"http://127.0.0.1:{self.port}/v1",
            "lmstudio_use_tools": True,
        }})

    def test_full_chat_turn_invokes_the_tool_and_uses_its_result(self):
        with tempfile.TemporaryDirectory() as viewer_dir:
            result, status = controllers.handle_chat(
                self.cfg, os.path.join(ROOT, "notes"), viewer_dir,
                {"message": "what is the capital of Egypt?"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(len(_StubLMStudioHandler.received), 2, "expected a tool round then an answer")

        # 1. the tool was offered, in OpenAI shape
        offered = [t["function"]["name"] for t in _StubLMStudioHandler.received[0]["tools"]]
        self.assertIn("web_search", offered)

        # 2. the real search ran and its result was fed back to the model
        tool_msg = next(m for m in _StubLMStudioHandler.received[1]["messages"]
                        if m.get("role") == "tool")
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["status"], "ok")
        self.assertIn("Cairo is the capital of Egypt", payload["results"][0]["snippet"])

        # 3. the model's answer reached the user, with the SOURCE line stripped
        #    out of the spoken text by app/images.py
        self.assertIn("Cairo", result["answer"])
        self.assertNotIn("SOURCE:", result["answer"])

    def test_a_search_failure_does_not_break_the_turn(self):
        ws._fetch = lambda _q: (_ for _ in ()).throw(urllib.error.URLError("offline"))
        with tempfile.TemporaryDirectory() as viewer_dir:
            result, status = controllers.handle_chat(
                self.cfg, os.path.join(ROOT, "notes"), viewer_dir,
                {"message": "what is the capital of Egypt?"},
            )
        self.assertEqual(status, 200)
        tool_msg = next(m for m in _StubLMStudioHandler.received[1]["messages"]
                        if m.get("role") == "tool")
        self.assertEqual(json.loads(tool_msg["content"])["status"], "error")
        self.assertIn("Cairo", result["answer"])   # turn still completed


class LiveSearchTests(unittest.TestCase):
    """One real request. Skipped when offline so the suite stays green."""

    def test_real_duckduckgo_search(self):
        out = ws.execute_web_search({"query": "capital of Egypt"})
        if out["status"] == "error":
            self.skipTest(f"network unavailable: {out['error']}")
        self.assertGreater(out["count"], 0)
        self.assertLessEqual(out["count"], ws.MAX_RESULTS)
        first = out["results"][0]
        self.assertTrue(first["url"].startswith("http"))
        self.assertNotIn("duckduckgo.com/l/", first["url"])
        print(f"\n    live result: {first['title'][:60]} -> {first['url'][:60]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
