"""app/connectors/web_fetch.py — read the text of one web page (Connector
layer).

The companion to web_search.py. Search returns snippets, and a snippet is
often a page *description* rather than the fact asked for: searching "weather
in Cairo" yields "Check current conditions in Cairo, Egypt with radar…" — which
names a source but contains no temperature. The model then has to either refuse
or invent a number. This tool closes that gap: search to find the page, fetch
to read it.

Standard library only — urllib for the request, html.parser for extraction, and
ipaddress/socket for the address checks below.

SECURITY — this is the dangerous half of the pair, and why this module is
longer than it looks like it should be. web_search only ever talks to one
hardcoded host; web_fetch dereferences a URL a language model chose, from a
process that (per config.json's default ``server.bind``) is reachable by the
whole LAN with no authentication, and that sits alongside private services:
the browser-automation daemon on 127.0.0.1:4701 and JARVIS's own HTTP server on
:4700. Left unguarded, "fetch this page" is a server-side request forgery
primitive able to read the daemon's endpoints, hit cloud metadata services on
169.254.169.254, or — since urllib installs a FileHandler by default — read
local files through ``file:///etc/passwd``.

Three guards, all in _assert_fetchable:
  1. Scheme allowlist — http and https only. Blocks file:, ftp:, gopher:, data:.
  2. Address check — the hostname is resolved and every resulting IP must be
     globally routable. Blocks loopback, RFC1918, link-local (including the
     metadata endpoint), CGNAT, multicast and reserved space, whether reached
     by literal IP or by a hostname that resolves there.
  3. Redirect re-validation — _GuardedRedirectHandler re-runs both checks on
     every hop, so a public URL cannot bounce the fetch to 127.0.0.1.

Residual risk, stated plainly: guard 2 resolves the name and then hands the
*name* to urllib, which resolves it again. A DNS entry that changes between the
two (rebinding) would slip through. Closing that needs pinning the connection
to the vetted IP, which urllib does not expose. The guard stops accidental and
opportunistic access, not a determined attacker who controls a DNS zone.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Content types worth handing to a language model. Anything else (images,
#: PDFs, archives, video) is reported by type rather than decoded as mojibake.
ALLOWED_CONTENT_TYPES = frozenset({
    "text/html", "application/xhtml+xml", "text/plain", "text/markdown",
    "application/json", "text/xml", "application/xml", "text/csv",
})

#: Default characters of extracted text returned. Whole articles blow up the
#: context window, and the tool result is re-read on every later turn of the
#: provider loop, so the cost is paid repeatedly.
DEFAULT_MAX_CHARS = 6000

#: Ceiling on what the model may ask for via `max_chars`.
HARD_MAX_CHARS = 20000

#: Bytes read off the wire before decoding. Independent of MAX_CHARS: a page
#: can be megabytes of markup that renders to very little text.
MAX_RESPONSE_BYTES = 3 * 1024 * 1024

REQUEST_TIMEOUT = 15

#: Statuses that mean "this site refuses automated readers", as opposed to
#: "this page is wrong or gone". They warrant trying a different source, so the
#: error returned for them says so explicitly — see execute_web_fetch.
BLOCKED_STATUS_CODES = frozenset({401, 402, 403, 405, 406, 429, 451})

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

#: Elements whose text is chrome, not content.
SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "canvas", "template", "iframe",
    "form", "nav", "footer", "aside", "select", "button", "menu", "dialog",
})

#: Elements that carry the main content when a page marks it up semantically.
#: Preferred over the whole body when they yield enough text — Wikipedia's
#: <main> excludes the sidebar and the 228-language list that otherwise ate
#: most of the character budget before reaching a single sentence of article.
MAIN_TAGS = frozenset({"main", "article"})

#: Void elements never get an end tag, so they must never open a region — a
#: skipped <img class="nav-icon"> would otherwise swallow the rest of the page.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

#: Structural elements that must never be classified as chrome by their
#: attributes. Wikipedia ships <body class="… vector-toc-available">, which
#: matches the "toc" token below — treating that as furniture discards the
#: entire document. A heuristic allowed to drop the root is not a heuristic.
NEVER_CHROME_TAGS = frozenset({"html", "body"})

#: ARIA landmark roles that denote page furniture rather than content.
CHROME_ROLES = frozenset({
    "navigation", "banner", "contentinfo", "complementary", "search",
    "menu", "menubar", "toolbar", "dialog", "alert",
})

#: id/class tokens that mark a container as furniture. Matched on whole
#: hyphen/underscore-separated tokens so "header" does not match "sub-header-x"
#: by accident, and kept deliberately conservative: over-skipping loses real
#: content, which is worse than leaving some navigation in.
CHROME_ATTR_RE = re.compile(
    r"(?:^|[\s_-])(?:"
    r"nav|navbar|navigation|sidebar|side-?nav|menu|footer|banner|masthead|"
    r"breadcrumbs?|pagination|paginate|cookie|consent|advert|ads?|promo|"
    r"newsletter|subscribe|social|share|sharing|related|recirc|comments?|"
    r"skip-?link|toc|table-of-contents|language-?list|langlinks|siteSub"
    r")(?:$|[\s_-])",
    re.IGNORECASE,
)

#: Elements that imply a line break, so the extracted text keeps its structure
#: instead of running together into one paragraph.
BLOCK_TAGS = frozenset({
    "p", "div", "br", "hr", "li", "tr", "section", "article", "blockquote",
    "pre", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "dl", "dt",
    "dd", "figcaption", "main", "header",
})

_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


# ---------------------------------------------------------------------------
# Tool schema — canonical Anthropic format
# ---------------------------------------------------------------------------

WEB_FETCH_SCHEMA: dict[str, Any] = {
    "name": "web_fetch",
    "description": (
        "Fetch one web page and return its readable text. Use this after web_search "
        "when the snippets name a source but do not contain the actual figure, quote "
        "or detail asked for — a temperature, a price, a score, a date, a "
        "specification. Pass a URL that came from a search result or that the user "
        "gave you; do not invent URLs. Only public http(s) pages can be read."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute http:// or https:// URL of the page to read.",
            },
            "max_chars": {
                "type": "integer",
                "description": (
                    f"Maximum characters of text to return (default {DEFAULT_MAX_CHARS}, "
                    f"maximum {HARD_MAX_CHARS}). Raise it only when the detail you need "
                    "is likely deep in a long page."
                ),
            },
        },
        "required": ["url"],
    },
}


# ---------------------------------------------------------------------------
# Address safety
# ---------------------------------------------------------------------------


class UnsafeURLError(ValueError):
    """The URL is syntactically fine but must not be fetched — wrong scheme, or
    an address inside the private network this server is running on."""


def _is_public_ip(raw: str) -> bool:
    """Whether `raw` is a globally routable address.

    ``is_global`` is the single check that matters; the explicit clauses are
    listed for the reader and for older behaviours where is_global alone was
    not enough. 169.254.169.254 (the cloud metadata endpoint) is covered by
    is_link_local.
    """
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    ) and ip.is_global


def _assert_fetchable(url: str) -> str:
    """Validate one URL (or redirect hop) and return it normalized.

    Raises:
        UnsafeURLError: bad scheme, missing host, unresolvable host, or any
            resolved address that is not globally routable.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError(f"malformed URL: {exc}") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"only http and https URLs can be fetched, not '{scheme or 'relative'}'."
        )

    host = parts.hostname
    if not host:
        raise UnsafeURLError("the URL has no host.")

    # A literal IP never needs resolving, and resolving it would mask what was
    # actually asked for.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _is_public_ip(host):
            raise UnsafeURLError(f"'{host}' is a private or local address and cannot be fetched.")
        return url

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve '{host}': {exc}") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeURLError(f"'{host}' resolved to no addresses.")
    # EVERY address must be public: a name resolving to both a public and a
    # private IP would otherwise be fetchable via the private one.
    for address in addresses:
        if not _is_public_ip(address):
            raise UnsafeURLError(
                f"'{host}' resolves to the private or local address {address} and cannot be fetched."
            )
    return url


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect hop.

    Without this, a public URL that 302s to http://127.0.0.1:4701/ would be
    followed happily — the checks in _assert_fetchable only ever saw the
    original URL.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_fetchable(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------


class HTMLTextExtractor(HTMLParser):
    """Reduces an HTML document to readable plain text.

    Not a renderer. Three passes' worth of work in one:

    1. *Chrome removal.* Elements in SKIP_TAGS are dropped wholesale, as is any
       container whose ARIA role is in CHROME_ROLES or whose id/class matches
       CHROME_ATTR_RE. The regions are tracked on a stack rather than by a
       depth counter, because a region opened by an *attribute* on a ``<div>``
       cannot be closed by testing the tag name of ``</div>`` alone.
    2. *Main-content preference.* Text inside ``<main>``/``<article>`` is
       collected separately and preferred when it yields enough of it. Most
       news sites and every MediaWiki page mark content up this way, and the
       difference is large: Wikipedia's ``<main>`` skips the sidebar and
       language list entirely.
    3. *Structure preservation.* BLOCK_TAGS insert newlines so paragraphs and
       table rows stay separated instead of running together.

    ``convert_charrefs`` stays at its default True, so entities arrive decoded.

    Attributes:
        title: The document's ``<title>``, or "".
        text: Extracted text, after ``close()``.
        used_main: Whether the ``<main>``/``<article>`` region was used —
            surfaced in the tool result so a thin extraction is diagnosable.
    """

    #: Below this many characters, a <main> region is treated as a nav shell
    #: rather than the article, and the whole-body text is used instead.
    MAIN_MIN_CHARS = 200

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.source = "body"
        # Three parallel collections, narrowest first. Keeping all three lets
        # `text` fall back when a narrower one turns out to be empty, so no
        # single heuristic can silently discard the page.
        self._main_chunks: list[str] = []   # inside <main>/<article>
        self._chunks: list[str] = []        # chrome-filtered body
        self._raw_chunks: list[str] = []    # only script/style-style tags dropped
        # (tag, reason) where reason is "hard" (SKIP_TAGS), "soft" (attribute
        # chrome) or None (a nested tag, tracked only so the right end tag
        # closes the region).
        self._skip_stack: list[tuple[str, str | None]] = []
        self._main_depth = 0
        self._in_title = False

    # -- chrome detection --------------------------------------------------

    @staticmethod
    def _is_chrome(tag: str, attrs) -> bool:
        """Whether a container's attributes mark it as page furniture."""
        if tag in NEVER_CHROME_TAGS:
            return False
        for key, value in attrs:
            if not value:
                continue
            if key == "role" and value.strip().lower() in CHROME_ROLES:
                return True
            if key in ("id", "class") and CHROME_ATTR_RE.search(value):
                return True
        return False

    @property
    def _hard_skipping(self) -> bool:
        return any(reason == "hard" for _tag, reason in self._skip_stack)

    def _emit(self, chunk: str) -> None:
        if not self._hard_skipping:
            self._raw_chunks.append(chunk)
        if not self._skip_stack:
            self._chunks.append(chunk)
            if self._main_depth:
                self._main_chunks.append(chunk)

    # -- HTMLParser interface ---------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._skip_stack:
            # Inside dropped content: track nesting so the matching end tag
            # closes the region, but still feed the raw collection when the
            # region was only dropped on an attribute guess.
            if tag not in VOID_TAGS:
                self._skip_stack.append((tag, None))
            if tag in BLOCK_TAGS:
                self._emit("\n")
            return

        if tag == "title":
            self._in_title = True
            return

        if tag not in VOID_TAGS:
            if tag in SKIP_TAGS:
                self._skip_stack.append((tag, "hard"))
                return
            if self._is_chrome(tag, attrs):
                self._skip_stack.append((tag, "soft"))
                return

        if tag in MAIN_TAGS:
            self._main_depth += 1
        if tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # <br/> and <hr/> never reach handle_endtag, so routing them through
        # handle_starttag would leave a region permanently open.
        if tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            # Unwind to the matching open tag. Popping to the *last* occurrence
            # rather than blindly popping one keeps malformed markup (a missing
            # </div>) from wedging the parser into skipping the whole page.
            tags = [t for t, _r in self._skip_stack]
            if tag in tags:
                index = len(tags) - 1 - tags[::-1].index(tag)
                del self._skip_stack[index:]
            elif tag in BLOCK_TAGS:
                self._emit("\n")
            return

        if tag == "title":
            self._in_title = False
            return
        if tag in BLOCK_TAGS:
            self._emit("\n")
        if tag in MAIN_TAGS:
            self._main_depth = max(0, self._main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        self._emit(data)

    # -- output ------------------------------------------------------------

    @staticmethod
    def _normalize(chunks: list[str]) -> str:
        joined = _INLINE_WS_RE.sub(" ", "".join(chunks))
        lines = [line.strip() for line in joined.split("\n")]
        return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()

    @property
    def text(self) -> str:
        """The extracted text, whitespace-normalized: spaces and tabs collapse
        within a line, runs of blank lines collapse to one.

        Narrowest usable region wins:

        1. ``<main>``/``<article>``, when it carried real content.
        2. The chrome-filtered body — unless filtering removed most of the
           page, which means a class or role matched something structural
           rather than furniture.
        3. The unfiltered body, so an over-eager guess degrades to a noisier
           extraction rather than to nothing at all.

        Sets ``source`` to which one was used; the tool reports it as
        ``extracted_from``, so a thin result is diagnosable without guesswork.
        """
        raw_text = self._normalize(self._raw_chunks)
        main_text = self._normalize(self._main_chunks)
        if len(main_text) >= self.MAIN_MIN_CHARS:
            self.source = "main"
            return main_text

        body_text = self._normalize(self._chunks)
        if len(body_text) >= self.MAIN_MIN_CHARS or len(body_text) >= len(raw_text) // 2:
            self.source = "body"
            return body_text

        self.source = "body-unfiltered"
        return raw_text


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

_opener = urllib.request.build_opener(_GuardedRedirectHandler)


def _fetch(url: str) -> tuple[str, str, str]:
    """Fetch `url`, returning (final_url, content_type, decoded_body)."""
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
        # urllib does not transparently decompress, so asking for gzip would
        # hand the parser binary data.
        "Accept-Encoding": "identity",
    })
    with _opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read(MAX_RESPONSE_BYTES)
        charset = response.headers.get_content_charset() or "utf-8"
        content_type = (response.headers.get_content_type() or "").lower()
        final_url = response.geturl()
    return final_url, content_type, raw.decode(charset, errors="replace")


def execute_web_fetch(arguments: dict) -> dict:
    """Fetch one page and return its readable text.

    Never raises, for the same reason as execute_web_search: this runs on the
    worker thread serving one HTTP request, and an escaping exception would end
    the turn with no answer. Every refusal explains itself in terms the model
    can repeat to the user — "that is a private address" is something JARVIS can
    say out loud.

    Args:
        arguments: ``{"url": str, "max_chars": int (optional)}``.

    Returns:
        On success::

            {"status": "ok", "url": <final URL after redirects>, "title": str,
             "content_type": str, "chars": int, "truncated": bool,
             "content": str}

        On failure, ``{"status": "error", "error": str}``.
    """
    if not isinstance(arguments, dict):
        return {"status": "error", "error": "web_fetch expects an object of arguments."}

    url = (arguments.get("url") or "").strip()
    if not url:
        return {"status": "error", "error": "web_fetch requires a non-empty 'url' string."}

    raw_max = arguments.get("max_chars")
    try:
        max_chars = int(raw_max) if raw_max is not None else DEFAULT_MAX_CHARS
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    max_chars = max(1, min(max_chars, HARD_MAX_CHARS))

    try:
        _assert_fetchable(url)
    except UnsafeURLError as exc:
        return {"status": "error", "error": f"Refusing to fetch that URL: {exc}"}

    try:
        final_url, content_type, body = _fetch(url)
    except UnsafeURLError as exc:
        # Raised from inside the redirect handler, mid-request.
        return {"status": "error", "error": f"Refusing to follow that redirect: {exc}"}
    except urllib.error.HTTPError as exc:
        # A bot block is not a dead end, but a small model will not remember a
        # rule buried at depth in a 20k-character system prompt. Putting the
        # next action in the tool result puts it in immediate context, where
        # the model is already reading — the difference between "I could not
        # retrieve it" and actually trying the next source.
        if exc.code in BLOCKED_STATUS_CODES:
            return {
                "status": "error",
                "retry_suggested": True,
                "error": (
                    f"That page returned HTTP {exc.code} ({exc.reason}) — this site "
                    "blocks automated readers. This is not a dead end: call web_fetch again "
                    "on a DIFFERENT result URL from your search. Reference and encyclopedia "
                    "sites (Wikipedia, official or government pages) almost always succeed "
                    "where commercial news, weather and finance sites refuse. Do not report "
                    "failure until several different sources have actually been tried."
                ),
            }
        return {"status": "error",
                "error": f"The page returned HTTP {exc.code} ({exc.reason})."}
    except urllib.error.URLError as exc:
        return {"status": "error", "error": f"Could not reach that page: {exc.reason}"}
    except (TimeoutError, OSError) as exc:
        return {"status": "error", "error": f"The request failed: {exc}"}
    except Exception as exc:  # noqa: BLE001 — deliberate: see docstring
        return {"status": "error", "error": f"Unexpected fetch failure: {exc}"}

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        return {"status": "error",
                "error": f"That URL is '{content_type}', not a readable text page."}

    extraction_source = "body"
    try:
        if content_type in ("text/html", "application/xhtml+xml"):
            parser = HTMLTextExtractor()
            parser.feed(body)
            parser.close()
            title, text = parser.title.strip(), parser.text
            extraction_source = parser.source   # set as a side effect of .text
        else:
            title, text = "", body.strip()
    except Exception as exc:  # noqa: BLE001 — malformed markup must not end the turn
        return {"status": "error", "error": f"Could not extract text from that page: {exc}"}

    if not text:
        return {"status": "ok", "url": final_url, "title": title,
                "content_type": content_type, "chars": 0, "truncated": False,
                "content": "",
                "extracted_from": extraction_source,
                "note": "The page was reachable but contained no extractable text "
                        "(it may be rendered entirely by JavaScript)."}

    truncated = len(text) > max_chars
    return {
        "status": "ok",
        "url": final_url,
        "title": title,
        "content_type": content_type,
        "chars": min(len(text), max_chars),
        "truncated": truncated,
        "extracted_from": extraction_source,
        "content": text[:max_chars],
    }
