"""app/connectors/web_search.py — provider-independent web search (Connector
layer).

Search used to be a capability of the *backend* rather than of JARVIS: the
Anthropic provider declared Anthropic's server-side ``web_search`` tool and the
Gemini provider declared Google's ``google_search``, each executed on the
vendor's own infrastructure. A local model served by LM Studio has no such
tool, so "what's the weather in Cairo" answered from training data or guessed,
and the instruction in app/persona.py to "use your web search tool" pointed at
nothing at all.

This module makes search a normal registry tool with a local handler: JARVIS
performs the request itself, so every provider gets identical behaviour and
the answer no longer depends on which backend happened to reply.

Source is DuckDuckGo Lite (https://lite.duckduckgo.com/lite/) — a minimal,
JavaScript-free HTML endpoint whose markup is a plain table, which is what
makes it parseable with html.parser and no dependencies. Standard library
only: urllib for the request, html.parser for the response. No requests, no
BeautifulSoup.

Being a scraper, this is inherently more fragile than a contracted API: DDG can
change its markup or rate-limit us. Every failure path is therefore contained
and reported as data (see execute_web_search), never raised — a search that
comes back empty must degrade to "I could not find that" rather than taking
down the turn.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

SEARCH_URL = "https://lite.duckduckgo.com/lite/"

#: Hard cap on returned results. Each snippet is roughly 30-60 tokens and the
#: whole payload is JSON-encoded into a tool_result the model re-reads on every
#: subsequent turn of the loop, so this is a context-budget decision, not a
#: display one.
MAX_RESULTS = 5

#: DuckDuckGo serves an empty result set to obvious bots. A real browser's
#: User-Agent is the minimum needed to be answered normally.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

#: A stalled search must not hold a chat turn open. The provider tool loops
#: already run long (Gemini allows 500 rounds), so a slow search compounds.
REQUEST_TIMEOUT = 12

#: Defensive ceiling on the response body. A normal Lite page is ~30 KB;
#: anything far past that is a redirect loop or an error page, and we would
#: rather truncate than feed the parser unbounded input.
MAX_RESPONSE_BYTES = 512 * 1024

_WHITESPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Tool schema — canonical Anthropic format, as every connector here uses
# ---------------------------------------------------------------------------

#: The full tool spec in the canonical Anthropic shape
#: (``{"name", "description", "input_schema"}``) — identical to what
#: get_gmail_tools() and friends return, so the registry converts it to
#: Gemini's and OpenAI's shapes with no special-casing.
WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the public web and return the top result snippets with their page "
        "titles and URLs. Use this for anything the notes cannot cover and anything "
        "time-sensitive or about the outside world — current events, prices, weather, "
        "sports results, 'what is X', 'who is X', release dates. Search first, then "
        "answer from what comes back rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query, phrased as you would type it into a search "
                    "engine. Resolve pronouns and back-references from the "
                    "conversation first: search 'Cairo weather today', never 'the "
                    "weather there'."
                ),
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


class DuckDuckGoLiteParser(HTMLParser):
    """Extracts search results from a DuckDuckGo Lite response.

    Lite renders each result as consecutive rows of one table::

        <tr><td><a class="result-link" href="...">TITLE</a></td></tr>
        <tr><td class="result-snippet">SNIPPET TEXT</td></tr>

    so results are assembled positionally: a ``result-link`` opens a new
    result, and the next ``result-snippet`` fills in the one most recently
    opened. A snippet arriving with no open result (markup drift, or an ad
    block rendered snippet-first) still lands as a title-less result rather
    than being dropped — the snippet text is the part the model actually
    needs.

    ``convert_charrefs`` is left at its default True, so ``handle_data``
    receives text with entities such as ``&amp;`` and ``&#39;`` already
    decoded; no unescaping is needed downstream.

    Attributes:
        results: Accumulated ``{"title", "url", "snippet"}`` dicts, in page
            order. Read it after ``feed()``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._in_snippet = False
        self._buffer: list[str] = []
        self._href = ""

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _has_class(attrs: list[tuple[str, str | None]], wanted: str) -> bool:
        """Whether the tag carries `wanted` among its CSS classes. Matches on
        whitespace-separated tokens so a future ``class="result-snippet foo"``
        keeps working."""
        for key, value in attrs:
            if key == "class" and value and wanted in value.split():
                return True
        return False

    @staticmethod
    def _attr(attrs: list[tuple[str, str | None]], wanted: str) -> str:
        for key, value in attrs:
            if key == wanted:
                return value or ""
        return ""

    def _flush(self) -> str:
        text = _WHITESPACE_RE.sub(" ", "".join(self._buffer)).strip()
        self._buffer = []
        return text

    # -- HTMLParser interface --------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._has_class(attrs, "result-link"):
            self._in_link = True
            self._buffer = []
            self._href = self._attr(attrs, "href")
        elif tag == "td" and self._has_class(attrs, "result-snippet"):
            self._in_snippet = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_link or self._in_snippet:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
            self.results.append({
                "title": self._flush(),
                "url": unwrap_redirect(self._href),
                "snippet": "",
            })
            self._href = ""
        elif tag == "td" and self._in_snippet:
            self._in_snippet = False
            snippet = self._flush()
            if self.results and not self.results[-1]["snippet"]:
                self.results[-1]["snippet"] = snippet
            elif snippet:
                self.results.append({"title": "", "url": "", "snippet": snippet})


def unwrap_redirect(href: str) -> str:
    """The real destination behind a DuckDuckGo redirect link.

    Lite wraps results as ``//duckduckgo.com/l/?uddg=<url-encoded target>&rut=…``.
    Handing that to the model would be useless — app/persona.py has it emit a
    ``SOURCE: <url>`` line the user may later ask to share — so the ``uddg``
    parameter is unwrapped back to the destination. Anything not shaped like a
    redirect is returned as-is, with a protocol added to protocol-relative
    links.
    """
    if not href:
        return ""
    try:
        parsed = urllib.parse.urlparse(href)
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target and target[0]:
            return target[0]
    except ValueError:
        return href
    if href.startswith("//"):
        return "https:" + href
    return href


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _fetch(query: str) -> str:
    """POST the query to DuckDuckGo Lite and return the decoded HTML.

    ``Accept-Encoding: identity`` is deliberate: urllib does not transparently
    decompress, so requesting gzip would hand the parser binary data.
    """
    body = urllib.parse.urlencode({"q": query}).encode("utf-8")
    request = urllib.request.Request(
        SEARCH_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read(MAX_RESPONSE_BYTES)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def execute_web_search(arguments: dict) -> dict:
    """Run one web search and return its results.

    Never raises. This runs inside the provider tool loop on a worker thread
    serving one HTTP request (app/http_server.py:47); an exception escaping
    here would end the turn with no answer at all. The registry's dispatch
    would also shield it, but a network tool should report *why* it failed in
    terms the model can relay to the user — "DuckDuckGo returned HTTP 429" is
    something JARVIS can say out loud, where a bare traceback is not.

    Args:
        arguments: ``{"query": str}``. See WEB_SEARCH_SCHEMA.

    Returns:
        On success::

            {"status": "ok", "query": str, "count": int,
             "results": [{"title": str, "url": str, "snippet": str}, ...]}

        capped at MAX_RESULTS. A search that genuinely found nothing is a
        success with ``count: 0`` and a ``note`` — not an error, since there is
        nothing wrong for the user to fix.

        On failure, ``{"status": "error", "error": str}``.
    """
    query = (arguments.get("query") or "").strip() if isinstance(arguments, dict) else ""
    if not query:
        return {"status": "error", "error": "web_search requires a non-empty 'query' string."}

    try:
        html = _fetch(query)
    except urllib.error.HTTPError as exc:
        # Raised before URLError below — HTTPError is a subclass of it, and the
        # status code is the useful part (429 = rate limited, 403 = blocked).
        return {
            "status": "error",
            "error": f"The search provider returned HTTP {exc.code} ({exc.reason}).",
        }
    except urllib.error.URLError as exc:
        return {"status": "error", "error": f"Could not reach the search provider: {exc.reason}"}
    except (TimeoutError, OSError) as exc:
        return {"status": "error", "error": f"The search request failed: {exc}"}
    except Exception as exc:  # noqa: BLE001 — deliberate: see docstring
        return {"status": "error", "error": f"Unexpected search failure: {exc}"}

    try:
        parser = DuckDuckGoLiteParser()
        parser.feed(html)
        parser.close()
        results = [r for r in parser.results if r["snippet"] or r["title"]][:MAX_RESULTS]
    except Exception as exc:  # noqa: BLE001 — malformed markup must not end the turn
        return {"status": "error", "error": f"Could not parse the search results: {exc}"}

    if not results:
        # Distinguishable from an error on purpose: the request worked, the
        # page just held nothing. Either a genuinely empty result set or DDG
        # changed its markup — the model should say so, not retry forever.
        return {
            "status": "ok",
            "query": query,
            "count": 0,
            "results": [],
            "note": "The search returned no usable results for this query.",
        }

    return {"status": "ok", "query": query, "count": len(results), "results": results}
