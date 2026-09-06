"""app/connectors/spotify.py — Spotify Web API integration & tools (Connector layer).

Provides tools to search and control Spotify playback (play track/album/artist/playlist,
pause, resume, stop, skip next, skip previous, volume control, get currently playing)
via the Spotify Web API using OAuth2 tokens and client credentials with zero external dependencies.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS_BASE = "https://accounts.spotify.com/api/token"


def get_spotify_tools() -> list[dict[str, Any]]:
    """Returns tool definitions for Spotify music control."""
    return [
        {
            "name": "spotify_play",
            "description": (
                "Play a specific song, artist, album, or playlist on Spotify via the Web API. "
                "If a query is provided, searches Spotify and begins playback on the active or configured device."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Song title, artist, album, or search term to play.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["track", "album", "artist", "playlist"],
                        "description": "Type of content to search and play (default: track).",
                        "default": "track",
                    },
                    "context_uri": {
                        "type": "string",
                        "description": "Optional Spotify URI (e.g. spotify:playlist:... or spotify:album:...) to play directly.",
                    },
                    "device_id": {
                        "type": "string",
                        "description": "Optional target Spotify device ID.",
                    },
                },
            },
        },
        {
            "name": "spotify_playback_control",
            "description": "Control Spotify playback state: pause, resume, stop, skip to next, skip to previous/go back, or check status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "resume", "stop", "next", "previous", "status"],
                        "description": "Playback action to execute on Spotify.",
                    },
                    "device_id": {
                        "type": "string",
                        "description": "Optional target Spotify device ID.",
                    },
                },
                "required": ["action"],
            },
        },
        {
            "name": "spotify_set_volume",
            "description": "Adjust Spotify player volume percentage (0-100) or relative step (+/-).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "volume_percent": {
                        "type": "integer",
                        "description": "Target volume percentage from 0 to 100.",
                    },
                    "step": {
                        "type": "integer",
                        "description": "Relative volume adjustment step (e.g. +10 or -10).",
                    },
                    "device_id": {
                        "type": "string",
                        "description": "Optional target Spotify device ID.",
                    },
                },
            },
        },
        {
            "name": "spotify_get_devices",
            "description": "List all available Spotify Connect devices and their active playback status.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "spotify_show_panel",
            "description": (
                "Reveal the now-playing panel in the JARVIS interface. The panel is hidden by "
                "default and only this tool opens it. It first checks for a live Spotify Connect "
                "device; if none exists it launches the desktop Spotify app and waits for it to "
                "register before revealing the panel. Use for 'open the Spotify panel/player', "
                "'show me what's playing', 'bring up the music card'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "spotify_hide_panel",
            "description": (
                "Hide the now-playing panel in the JARVIS interface again. Use for 'close the "
                "Spotify panel', 'hide the music card'. Closing Spotify itself also dismisses it "
                "automatically."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
    ]


# ---------------------------------------------------------------------------
# Token lifecycle.
#
# config.json is re-read on every request, so a token refreshed mid-session
# would be forgotten by the next turn. This module-level cache is what carries
# a refreshed token between requests, and _DEAD_TOKENS remembers a statically
# configured token once Spotify has rejected it, so we stop re-sending a corpse
# on every subsequent call and go straight to the refresh token instead.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Panel visibility.
#
# The now-playing card is invisible until someone asks for it. This flag is the
# single source of truth: the viewer polls it, spotify_show_panel raises it
# (only after a live Connect device exists), and spotify_hide_panel — or
# Spotify itself disappearing — lowers it again.
# ---------------------------------------------------------------------------

# A phantom Connect device (a phone, a stale web player) can outlive the
# desktop app by a long way, so "device exists" is not proof anything is
# listening. After this many consecutive polls with nothing loaded in the
# player, the panel stops claiming otherwise and dismisses itself.
_IDLE_POLL_LIMIT = 10

_PANEL: dict[str, Any] = {"visible": False, "idle_polls": 0}


def spotify_panel_visible() -> bool:
    return bool(_PANEL["visible"])


def set_spotify_panel_visible(visible: bool) -> None:
    _PANEL["visible"] = bool(visible)
    # Any deliberate open/close starts the idle count over; a freshly opened
    # panel must not inherit the staleness of the last one.
    _PANEL["idle_polls"] = 0


_TOKEN_CACHE: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
_DEAD_TOKENS: set[str] = set()


def _refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str | None:
    """Mints a fresh user access token from the refresh token, caching its expiry."""
    try:
        import base64
        auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode("utf-8")

        req = urllib.request.Request(
            SPOTIFY_ACCOUNTS_BASE,
            data=data,
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        token = (payload.get("access_token") or "").strip()
        if not token:
            return None
        try:
            ttl = float(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        # A minute of slack so a token never expires halfway through a request.
        _TOKEN_CACHE["access_token"] = token
        _TOKEN_CACHE["expires_at"] = time.time() + max(ttl - 60.0, 30.0)
        return token
    except Exception:
        return None


def _refresh_from_config(cfg: Any) -> str | None:
    """Refresh using whatever client credentials config.json / the env supply."""
    client_id = cfg.get("spotify.client_id") or os.environ.get("SPOTIFY_CLIENT_ID") or ""
    client_secret = cfg.get("spotify.client_secret") or os.environ.get("SPOTIFY_CLIENT_SECRET") or ""
    refresh_token = cfg.get("spotify.refresh_token") or os.environ.get("SPOTIFY_REFRESH_TOKEN") or ""
    parts = [str(client_id).strip(), str(client_secret).strip(), str(refresh_token).strip()]
    if not all(parts) or any(v.startswith("YOUR-") or v.startswith("PUT-YOUR") for v in parts):
        return None
    return _refresh_access_token(*parts)


def _get_token(cfg: Any, force_refresh: bool = False) -> str | None:
    """Resolves a live Spotify access token.

    Normal path: a still-valid cached token, else the configured one (unless
    Spotify has already rejected it), else a freshly minted one. force_refresh
    is the 401 retry path — it throws the cache away and mints a new token.
    """
    if force_refresh:
        _TOKEN_CACHE["access_token"] = None
        _TOKEN_CACHE["expires_at"] = 0.0
        return _refresh_from_config(cfg)

    cached = _TOKEN_CACHE.get("access_token")
    if cached and float(_TOKEN_CACHE.get("expires_at") or 0.0) > time.time():
        return cached

    configured = cfg.get("spotify.access_token") or os.environ.get("SPOTIFY_ACCESS_TOKEN") or ""
    configured = str(configured).strip()
    if configured and not configured.startswith("YOUR-") and configured not in _DEAD_TOKENS:
        return configured

    return _refresh_from_config(cfg)


def _make_spotify_request(
    token: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    body_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{SPOTIFY_API_BASE}/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    data = json.dumps(body_data).encode("utf-8") if body_data is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8")
            if not content.strip():
                return {"status": "ok", "http_code": resp.status}
            return json.loads(content)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
            msg = parsed.get("error", {}).get("message") or err_body
        except Exception:
            msg = err_body
        return {"status": "error", "http_code": e.code, "error": msg}


class _SpotifyCaller:
    """A Spotify caller that heals its own expired token.

    An access token lives an hour, so the interesting failure is not "no token"
    but "the token died since the last song". Every request goes through here:
    a 401 buries that token, refreshes once, and retries the same request, so
    playback never surfaces an auth error the user has to fix by hand.
    """

    def __init__(self, cfg: Any, token: str):
        self.cfg = cfg
        self.token = token

    def __call__(self, endpoint, params=None, method="GET", body_data=None):
        res = _make_spotify_request(self.token, endpoint, params=params, method=method, body_data=body_data)
        if not (isinstance(res, dict) and res.get("status") == "error" and res.get("http_code") == 401):
            return res

        _DEAD_TOKENS.add(self.token)
        fresh = _get_token(self.cfg, force_refresh=True)
        if not fresh or fresh == self.token:
            return res
        self.token = fresh
        return _make_spotify_request(fresh, endpoint, params=params, method=method, body_data=body_data)


def _list_devices(cfg: Any) -> list[dict[str, Any]]:
    """Live Spotify Connect devices, or [] when there are none / no token."""
    token = _get_token(cfg)
    if not token:
        return []
    try:
        res = _SpotifyCaller(cfg, token)("me/player/devices")
    except Exception:
        return []
    if not isinstance(res, dict) or res.get("status") == "error":
        return []
    return [d for d in (res.get("devices") or []) if isinstance(d, dict)]


def _exec_show_panel(cfg: Any) -> dict[str, Any]:
    """Reveal the panel, launching Spotify first when nothing is listening.

    The panel is only raised once a device has actually registered — showing a
    card for a player that does not exist is exactly the lie this panel was
    built to stop telling.
    """
    devices = _list_devices(cfg)
    if devices:
        set_spotify_panel_visible(True)
        active = next((d for d in devices if d.get("is_active")), devices[0])
        return {
            "status": "panel_shown",
            "launched": False,
            "device": active.get("name"),
            "device_count": len(devices),
        }

    from .system import execute_system_tool
    launch = execute_system_tool(cfg, "system_launch_app", {"app_name": "spotify"})
    if isinstance(launch, dict) and launch.get("error"):
        return {
            "status": "error",
            "error": f"No Spotify device was live and launching Spotify failed: {launch['error']}",
        }

    # Spotify takes a few seconds to appear on the Connect device list; the
    # panel stays hidden until it does.
    deadline = time.time() + 25.0
    while time.time() < deadline:
        time.sleep(2.0)
        devices = _list_devices(cfg)
        if devices:
            set_spotify_panel_visible(True)
            return {
                "status": "panel_shown",
                "launched": True,
                "device": devices[0].get("name"),
                "device_count": len(devices),
            }

    return {
        "status": "error",
        "launched": True,
        "error": (
            "Spotify was launched but no Connect device registered within 25s, so the panel "
            "was left hidden."
        ),
    }


def execute_spotify_tool(cfg: Any, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Executes a Spotify API tool call."""
    if tool_name == "spotify_show_panel":
        return _exec_show_panel(cfg)
    if tool_name == "spotify_hide_panel":
        set_spotify_panel_visible(False)
        return {"status": "panel_hidden"}

    token = _get_token(cfg)
    if not token:
        return {
            "status": "error",
            "error": (
                "No usable Spotify token. Set 'spotify.client_id', 'spotify.client_secret' and "
                "'spotify.refresh_token' in config.json once and tokens refresh themselves from then on."
            ),
        }
    call = _SpotifyCaller(cfg, token)

    device_id = tool_input.get("device_id") or cfg.get("spotify.device_id") or None
    dev_params = {"device_id": device_id} if device_id else None

    try:
        if tool_name == "spotify_play":
            query = tool_input.get("query")
            q_type = tool_input.get("type", "track")
            context_uri = tool_input.get("context_uri")

            if context_uri:
                body = {"context_uri": context_uri}
                res = call("me/player/play", params=dev_params, method="PUT", body_data=body)
                if res.get("status") == "error":
                    return res
                return {"status": "playing", "context_uri": context_uri}

            if query:
                search_res = call("search", params={"q": query, "type": q_type, "limit": 1})
                if search_res.get("status") == "error":
                    return search_res

                items_key = f"{q_type}s"
                items = search_res.get(items_key, {}).get("items", [])
                if not items:
                    return {"status": "not_found", "query": query, "type": q_type}

                top_item = items[0]
                item_uri = top_item.get("uri")
                item_name = top_item.get("name")
                artist_name = (
                    top_item.get("artists", [{}])[0].get("name")
                    if top_item.get("artists")
                    else None
                )

                if q_type in ("album", "playlist", "artist"):
                    body = {"context_uri": item_uri}
                else:
                    body = {"uris": [item_uri]}

                play_res = call("me/player/play", params=dev_params, method="PUT", body_data=body)
                if play_res.get("status") == "error":
                    return play_res

                return {
                    "status": "playing",
                    "name": item_name,
                    "artist": artist_name,
                    "uri": item_uri,
                    "type": q_type,
                }

            # If no query/uri provided, resume playback
            res = call("me/player/play", params=dev_params, method="PUT", body_data={})
            if res.get("status") == "error":
                return res
            return {"status": "playing", "message": "Playback resumed."}

        elif tool_name == "spotify_playback_control":
            action = tool_input.get("action", "status")

            if action == "pause" or action == "stop":
                res = call("me/player/pause", params=dev_params, method="PUT")
                if res.get("status") == "error":
                    return res
                return {"status": "paused", "action": action}

            elif action == "resume":
                res = call("me/player/play", params=dev_params, method="PUT", body_data={})
                if res.get("status") == "error":
                    return res
                return {"status": "playing", "action": "resumed"}

            elif action == "next":
                res = call("me/player/next", params=dev_params, method="POST")
                if res.get("status") == "error":
                    return res
                return {"status": "skipped_to_next"}

            elif action == "previous":
                res = call("me/player/previous", params=dev_params, method="POST")
                if res.get("status") == "error":
                    return res
                return {"status": "skipped_to_previous"}

            elif action == "status":
                res = call("me/player/currently-playing")
                if res.get("status") == "error":
                    return res
                if not res or not res.get("item"):
                    return {"status": "idle", "is_playing": False}
                item = res.get("item", {})
                return {
                    "status": "playing" if res.get("is_playing") else "paused",
                    "track": item.get("name"),
                    "artist": item.get("artists", [{}])[0].get("name") if item.get("artists") else "Unknown",
                    "album": item.get("album", {}).get("name"),
                    "is_playing": res.get("is_playing"),
                }

        elif tool_name == "spotify_set_volume":
            vol = tool_input.get("volume_percent")
            step = tool_input.get("step")

            if vol is None and step is not None:
                # Need current volume to adjust by step
                state = call("me/player")
                curr_vol = state.get("device", {}).get("volume_percent", 50) if isinstance(state, dict) else 50
                vol = max(0, min(100, curr_vol + int(step)))

            if vol is not None:
                vol = max(0, min(100, int(vol)))
                params = {"volume_percent": vol}
                if device_id:
                    params["device_id"] = device_id
                res = call("me/player/volume", params=params, method="PUT")
                if res.get("status") == "error":
                    return res
                return {"status": "success", "volume_percent": vol}

            return {"status": "error", "error": "Either volume_percent or step must be provided."}

        elif tool_name == "spotify_get_devices":
            res = call("me/player/devices")
            if res.get("status") == "error":
                return res
            devices = res.get("devices", [])
            return {"status": "ok", "devices": devices, "count": len(devices)}

        return {"status": "error", "error": f"Unknown Spotify tool: {tool_name}"}

    except Exception as exc:
        return {"status": "error", "error": f"Spotify operation failed: {exc}"}


class SpotifyConnector:
    """Convenience wrapper class around Spotify tools."""

    def __init__(self, cfg: Any = None):
        self.cfg = cfg

    def get_tools(self) -> list[dict[str, Any]]:
        return get_spotify_tools()

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        return execute_spotify_tool(self.cfg, tool_name, tool_input)



# ---------------------------------------------------------------------------
# Viewer-facing playback snapshot.
#
# The tool-layer "status" action above answers the LLM's question ("what is
# playing?") in prose-sized fields. The now-playing panel in the viewer needs
# more than that to draw itself: album art, elapsed/total position, which
# device is actually producing sound, and the device volume — precisely the
# facts that were invisible when playback silently landed on the wrong device.
# ---------------------------------------------------------------------------

def spotify_playback_snapshot(cfg: Any) -> dict[str, Any]:
    """Rich, UI-shaped view of the current Spotify player state."""
    # Hidden means hidden: no token work, no API calls, nothing for the viewer
    # to draw. spotify_show_panel is the only thing that opens this door.
    if not spotify_panel_visible():
        return {"status": "hidden", "panel_visible": False}

    # A panel that cannot be filled is worse than no panel: it sits there
    # reading "NOT CONFIGURED" over a player the user already closed. Every
    # branch that fails to produce playback state now lowers the flag.
    token = _get_token(cfg)
    if not token:
        set_spotify_panel_visible(False)
        return {
            "status": "hidden",
            "panel_visible": False,
            "reason": "spotify_unconfigured",
            "error": (
                "No usable Spotify token. Set 'spotify.client_id', 'spotify.client_secret' and "
                "'spotify.refresh_token' in config.json."
            ),
        }

    try:
        res = _SpotifyCaller(cfg, token)("me/player")
    except Exception as exc:
        set_spotify_panel_visible(False)
        return {"status": "hidden", "panel_visible": False, "reason": "spotify_error",
                "error": f"Spotify operation failed: {exc}"}

    if isinstance(res, dict) and res.get("status") == "error":
        set_spotify_panel_visible(False)
        return {"status": "hidden", "panel_visible": False, "reason": "spotify_error",
                "error": res.get("error"), "http_code": res.get("http_code")}

    # Spotify answers 204/no-content when nothing holds the player at all.
    if not isinstance(res, dict) or not res.get("item"):
        # Nothing playing is normal; no device at all means Spotify was closed,
        # and the panel dismisses itself rather than lingering over a corpse.
        if not _list_devices(cfg):
            set_spotify_panel_visible(False)
            return {"status": "hidden", "panel_visible": False, "reason": "spotify_closed"}

        # Devices exist but nothing is loaded. That is normal for a moment
        # between tracks and permanent for a stale phone still registered on
        # Connect, so give it a grace period and then stop pretending.
        _PANEL["idle_polls"] = int(_PANEL.get("idle_polls") or 0) + 1
        if _PANEL["idle_polls"] >= _IDLE_POLL_LIMIT:
            set_spotify_panel_visible(False)
            return {"status": "hidden", "panel_visible": False, "reason": "idle_timeout"}
        return {"status": "idle", "panel_visible": True, "is_playing": False, "device": None}

    # Real playback state: the panel has something true to show again.
    _PANEL["idle_polls"] = 0

    item = res.get("item") or {}
    album = item.get("album") or {}
    images = album.get("images") or []
    # images come largest-first; the middle one is plenty for a 96px tile.
    art = (images[1] if len(images) > 1 else (images[0] if images else {})).get("url")
    device = res.get("device") or {}

    return {
        "status": "playing" if res.get("is_playing") else "paused",
        "panel_visible": True,
        "is_playing": bool(res.get("is_playing")),
        "track": item.get("name"),
        "artist": ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a.get("name")) or None,
        "album": album.get("name"),
        "art_url": art,
        "track_url": (item.get("external_urls") or {}).get("spotify"),
        "progress_ms": res.get("progress_ms") or 0,
        "duration_ms": item.get("duration_ms") or 0,
        "shuffle": bool(res.get("shuffle_state")),
        "repeat": res.get("repeat_state"),
        "device": device.get("name"),
        "device_type": device.get("type"),
        "volume_percent": device.get("volume_percent"),
    }
