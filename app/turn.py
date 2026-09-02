"""app/turn.py — what the user actually asked for, available to code deep
inside a tool call (Model layer: request context).

A tool handler runs several layers below the message that triggered it and has
no way to see it, so it cannot tell an explicit instruction from the model's
own idea. That distinction is the whole of the display-routing policy: "play
this on YouTube" should play inside the interface, while "open YouTube in the
browser" should reach the real browser — same tool, same URL, different intent,
and only the user's own words separate them.

Bound per worker thread by the chat controller, exactly like telemetry's job
binding. When nothing is bound — the CLI, the tests, the Novatek autopilot —
`asked_for_browser()` and `builtin_handler_for()` still work, but
`browser_policy_violation()` enforces nothing, because there is no user request
to interpret and refusing there would break automation that legitimately drives
a real browser.
"""
import contextvars
import re
from urllib.parse import urlparse

_message = contextvars.ContextVar("jarvis_user_message", default="")

#: Naming the browser is the explicit opt-out: say it and the real browser is
#: the ONLY correct destination. Kept to words that unambiguously mean the
#: application — "open a new tab" is about the browser, "open the window
#: blinds" is not.
_BROWSER_WORDS = re.compile(
    r"\b(browser|chrome|chromium|firefox|edge|safari|incognito|new tab|"
    r"in a tab|address bar)\b", re.IGNORECASE)

#: These mirror what viewer/js/view/referenceWindows.js can ACTUALLY render,
#: pattern for pattern. That correspondence is the point: this guard refuses
#: the real browser by promising an in-interface window instead, so promising
#: one that renders blank would be worse than not guarding at all. A bare
#: https://youtube.com is the case in question — no video id, nothing to
#: embed, and YouTube refuses to be iframed — so it is deliberately NOT
#: claimed here and correctly falls through to the browser.
_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/|.+\?v=)|youtu\.be/)"
    r"([a-zA-Z0-9_-]{11})", re.IGNORECASE)
_VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?([0-9]+)", re.IGNORECASE)
_DIRECT_VIDEO_RE = re.compile(r"\.(mp4|webm|ogg|m4v)(\?.*)?$", re.IGNORECASE)
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif")

#: Hosts that exist to play video. A URL here that is NOT a playable video —
#: a search page, the homepage, a channel — is the shape the model reaches for
#: when it gives up on finding the actual track, and it is never the right
#: answer to "play X".
_VIDEO_HOST_RE = re.compile(
    r"(^|\.)(youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com)$", re.IGNORECASE)

#: Asking to PLAY something is a request about the content, never about which
#: application shows it. Verbs only, so "display", "playstore" and "replay"
#: cannot trip it; "open" is deliberately absent because "open Novatek" is a
#: legitimate browser request.
_PLAY_INTENT_RE = re.compile(
    r"\b(play|playing|watch|watching|listen|listening|stream|streaming)\b",
    re.IGNORECASE)


#: Unambiguous "put it in front of me" verbs. "open" is deliberately absent:
#: "open Novatek" is a legitimate request for the real browser, and losing that
#: would break the automation this app exists to do.
_SHOW_INTENT_RE = re.compile(
    r"\b(show|shows|see|view|display|pull\s+up|bring|look\s+at)\b", re.IGNORECASE)

_IMAGE_WORD_RE = re.compile(
    r"\b(image|images|picture|pictures|photo|photos|pic|pics|wallpaper|"
    r"wallpapers|screenshot|screenshots|artwork)\b", re.IGNORECASE)

#: Sites this app OPERATES rather than reads — logs into, clicks through,
#: fills forms in. A display rule must never touch these: an embedded viewer
#: cannot drive a Flutter app behind a login, so refusing the real browser here
#: would break the Novatek workflow outright. Override with
#: `browser.automation_hosts` in config.json.
DEFAULT_AUTOMATION_HOSTS = ("nec-dev.autotrial.app",)


def wants_display(text=None):
    return bool(_SHOW_INTENT_RE.search(text if text is not None else _message.get()))


def wants_images(text=None):
    return bool(_IMAGE_WORD_RE.search(text if text is not None else _message.get()))


def is_automation_host(url, hosts=DEFAULT_AUTOMATION_HOSTS):
    if not isinstance(url, str) or not url.strip():
        return False
    host = (urlparse(url.strip()).hostname or "").lower()
    return any(host == h.lower() or host.endswith("." + h.lower())
               for h in (hosts or ()) if h)


def is_video_host(url):
    if not isinstance(url, str) or not url.strip():
        return False
    return bool(_VIDEO_HOST_RE.search((urlparse(url.strip()).hostname or "")))


def wants_playback(text=None):
    return bool(_PLAY_INTENT_RE.search(text if text is not None else _message.get()))


def bind_message(text):
    """Marks this thread as serving that user message."""
    _message.set(str(text or ""))


def user_message():
    return _message.get()


def asked_for_browser(text=None):
    """True when the user named the browser themselves."""
    return bool(_BROWSER_WORDS.search(text if text is not None else _message.get()))


def builtin_handler_for(url):
    """Which in-interface surface can display this URL, if any.

    Returns "video", "image", or None. Deliberately narrow: it names only the
    cases the interface handles with CERTAINTY. An ordinary web page is left
    out on purpose — the SHOW window can only iframe a site that permits it,
    so a page is a preference expressed in the prompt, never a hard rule
    enforced here.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()

    if _YOUTUBE_RE.search(url) or _VIMEO_RE.search(url) or _DIRECT_VIDEO_RE.search(url):
        return "video"
    if (urlparse(url).path or "").lower().endswith(_IMAGE_EXT):
        return "image"
    return None


#: What to tell the model instead, per handler. Written as an instruction it
#: can act on in the same turn, not as a complaint — a refusal the model cannot
#: act on just becomes an apology to the user.
_REDIRECT = {
    "video": ("emit a line `VIDEO: {url}` in your reply — the interface plays it "
              "inside its own video window"),
    "image": ("emit a line `IMAGE: {url}` in your reply — the interface shows it "
              "inside its own image window"),
}


def browser_policy_violation(url, automation_hosts=DEFAULT_AUTOMATION_HOSTS):
    """The refusal text when opening `url` in the real browser goes against
    what the user asked for, or None when it is allowed.

    Allowed whenever: no chat turn is bound (automation), the user named the
    browser, the target is a site this app operates rather than displays, or
    nothing about the request says it belongs in the interface.
    """
    if not _message.get():
        return None
    if asked_for_browser():
        return None

    handler = builtin_handler_for(url)
    if handler is not None:
        return (
            f"Not opening the machine browser. The interface has a built-in "
            f"{handler} window for this, and the user did not ask for the browser. "
            f"Instead, {_REDIRECT[handler].format(url=url)}. "
            f"Use browser_open_url only when the user actually says browser, Chrome, "
            f"or a tab — or when nothing built in can display the target."
        )

    # A video host that is not a playable video: a search page, a channel, the
    # homepage. The URL alone looks innocent, so the earlier check passes it —
    # but as an answer to "play X" it is the model giving up on finding the
    # track and dumping search results into a browser the user never asked for.
    # The fix is not a different window, it is finding the actual video.
    if wants_playback() and is_video_host(url):
        return (
            "Not opening the machine browser. The user asked to play something and did "
            "not mention the browser, and this URL is a search or listing page rather "
            "than a playable video. Do not open it. Find the actual video first — use "
            "web_search for the specific track — then emit one `VIDEO: <watch url>` line "
            "so it plays inside the interface. Only if you genuinely cannot find a video "
            "URL, say so in plain words instead of opening a browser."
        )

    # "Show me X" is a request to be shown, not a request for a browser. This
    # is the last branch because it is intent-only: the URL looks perfectly
    # ordinary, and nothing about it reveals that shipping it to another
    # application is the wrong answer.
    if wants_display() and not is_automation_host(url, automation_hosts):
        if wants_images():
            return (
                "Not opening the machine browser. The user asked to be shown images and "
                "did not mention the browser. Do not open a browser or an image-search "
                "page. Find real direct image URLs — use web_search, then web_fetch the "
                "page if you need the actual file URLs — and emit one `IMAGE: <url>` line "
                "per image; the interface shows them in its own windows."
            )
        return (
            f"Not opening the machine browser. The user asked to be SHOWN this and did "
            f"not mention the browser. Emit exactly one line `SHOW: {url}` instead — the "
            f"interface opens it in a large embedded viewer. If the site refuses to be "
            f"embedded the viewer falls back to readable text, so this always shows "
            f"something. Use browser_open_url only when the user names the browser, or "
            f"for a site you must log into and operate rather than read."
        )

    return None
