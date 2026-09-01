#!/usr/bin/env python3
"""tools/browser_daemon.py — JARVIS's hands on a real browser (Playwright Async API + Flutter Automation).

Runs OUTSIDE the stdlib-only JARVIS server because it needs the playwright
pip package — launch it with .venv-browser/bin/python (see
tools/setup_browser.sh). The JARVIS server's browser connector
(app/connectors/browser.py) starts this on demand and talks to it over
http://127.0.0.1:4701.

Features:
- Native Playwright Async API + Asyncio Server: Completely eliminates 'Playwright Sync API inside asyncio loop' error.
- Full machine & tab state awareness (total tabs, active tab index, titles & URLs).
- Persistent profile support preserving user logins, cookies, and bookmarks.
- Automatic SingletonLock and stale process cleanup to prevent frozen about:blank pages.
- Tab management: tab reuse, new tab creation, tab listing, tab switching, tab closing.
- Flutter Web Automation: auto-detects Flutter Web (CanvasKit/HTML), activates semantics,
  extracts Flutter widgets, and performs canvas coordinate clicks & text editing.
- Flutter Testing Integration: supports running Patrol tests, Flutter integration tests,
  and Appium/Flutter drivers with local Flutter SDKs.
- Clean process lifecycle: prevents orphaned Chromium processes and zombie windows.
- Non-destructive error handling: element timeouts no longer discard the live browser window.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zlib

from playwright.async_api import (
    async_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

PORT = 4701
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _TimestampedStream:
    """Prefixes every log line with a timestamp so incidents in the append-only
    daemon log can be told apart from stale entries of past runs."""

    def __init__(self, stream):
        self._stream = stream
        self._at_line_start = True

    def write(self, text):
        if not text:
            return
        out = []
        for chunk in text.splitlines(keepends=True):
            if self._at_line_start and chunk.strip():
                out.append(time.strftime("[%Y-%m-%d %H:%M:%S] "))
            out.append(chunk)
            self._at_line_start = chunk.endswith("\n")
        self._stream.write("".join(out))
        self._stream.flush()

    def flush(self):
        self._stream.flush()


sys.stderr = _TimestampedStream(sys.stderr)

state = {
    "pw": None,
    "browser": None,
    "context": None,
    "current_profile": None,
    "active_tab_index": 0,
    # True while cmd_upload_file drives a chooser itself; the auto-handler
    # stays out of the way so the explicit file wins.
    "expecting_file_chooser": False,
}


def _find_flutter_binaries():
    """Discovers available Flutter, Dart, Patrol, and FVM binaries on the host."""
    candidates = [
        "/home/proslayer/development/flutter/bin/flutter",
        "/home/proslayer/development/3.35.6/bin/flutter",
        "/home/proslayer/development/3.29.1/bin/flutter",
        "/home/proslayer/development/3.19.0/bin/flutter",
        os.path.expanduser("~/.pub-cache/bin/fvm"),
        os.path.expanduser("~/.pub-cache/bin/patrol"),
    ]
    discovered = {}
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            name = os.path.basename(p)
            if name not in discovered:
                discovered[name] = p

    if shutil.which("flutter") and "flutter" not in discovered:
        discovered["flutter"] = shutil.which("flutter")
    if shutil.which("dart") and "dart" not in discovered:
        discovered["dart"] = shutil.which("dart")
    if shutil.which("patrol") and "patrol" not in discovered:
        discovered["patrol"] = shutil.which("patrol")
    if shutil.which("fvm") and "fvm" not in discovered:
        discovered["fvm"] = shutil.which("fvm")

    return discovered


def _clean_orphaned_playwright_processes():
    """Kills any stray Playwright temporary Chromium processes to prevent clutter."""
    try:
        subprocess.run(
            ["pkill", "-f", "playwright_chromiumdev_profile"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        pass


def _clean_profile_locks(profile_dir):
    """Kills any stale process holding SingletonLock or running with profile_dir and removes lock symlinks."""
    if not profile_dir or not os.path.isdir(profile_dir):
        return

    try:
        subprocess.run(
            ["pkill", "-9", "-f", f"user-data-dir={profile_dir}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except Exception:
        pass

    singleton_lock = os.path.join(profile_dir, "SingletonLock")
    if os.path.islink(singleton_lock) or os.path.exists(singleton_lock):
        try:
            target = os.readlink(singleton_lock) if os.path.islink(singleton_lock) else ""
            if "-" in target:
                pid_str = target.rsplit("-", 1)[-1]
                if pid_str.isdigit():
                    pid = int(pid_str)
                    try:
                        os.kill(pid, 9)
                    except (ProcessLookupError, PermissionError):
                        pass
        except Exception:
            pass

    for item in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(profile_dir, item)
        try:
            if os.path.islink(p) or os.path.exists(p):
                os.unlink(p)
        except Exception:
            pass


def get_chrome_profiles():
    """Scans ~/.config/google-chrome and ~/.config/chromium for real user profiles."""
    profiles = []
    base_dirs = [
        os.path.expanduser("~/.config/google-chrome"),
        os.path.expanduser("~/.config/chromium"),
    ]
    seen_ids = set()
    for base in base_dirs:
        local_state_path = os.path.join(base, "Local State")
        if not os.path.exists(local_state_path):
            continue
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            info_cache = data.get("profile", {}).get("info_cache", {})
            for profile_id, info in info_cache.items():
                name = info.get("name", profile_id)
                user_name = info.get("user_name", "")
                slug = name.lower().replace(" ", "_")
                if slug in seen_ids:
                    continue
                seen_ids.add(slug)
                profiles.append({
                    "id": profile_id,
                    "name": name,
                    "user_name": user_name,
                    "slug": slug,
                    "source_dir": os.path.join(base, profile_id),
                    "browser": "google-chrome" if "google-chrome" in base else "chromium",
                })
        except Exception as e:
            print(f"[browser-daemon] error reading profiles from {local_state_path}: {e}", file=sys.stderr)

    jarvis_base = os.path.expanduser("~/.config/jarvis-chrome")
    if os.path.isdir(jarvis_base):
        for entry in os.listdir(jarvis_base):
            entry_path = os.path.join(jarvis_base, entry)
            if os.path.isdir(entry_path) and entry not in seen_ids:
                profiles.append({
                    "id": entry,
                    "name": entry.replace("_", " ").title(),
                    "user_name": "",
                    "slug": entry.lower(),
                    "source_dir": entry_path,
                    "browser": "jarvis-persistent",
                })
                seen_ids.add(entry.lower())

    return profiles


def _resolve_profile_dir(profile_query):
    """Maps a profile name/query to a safe persistent directory in ~/.config/jarvis-chrome/."""
    jarvis_base = os.path.expanduser("~/.config/jarvis-chrome")
    os.makedirs(jarvis_base, exist_ok=True)

    if not profile_query or profile_query.strip().lower() in ("default", "none", ""):
        profile_slug = "default"
        target_dir = os.path.join(jarvis_base, "default")
        os.makedirs(target_dir, exist_ok=True)
        return target_dir, "default"

    query_clean = profile_query.strip().lower()
    discovered = get_chrome_profiles()

    matched = None
    for p in discovered:
        if (
            p["slug"] == query_clean
            or p["name"].lower() == query_clean
            or p["id"].lower() == query_clean
            or query_clean in p["name"].lower()
        ):
            matched = p
            break

    if matched:
        slug = re.sub(r"[^a-z0-9_]", "_", matched["name"].lower())
        target_dir = os.path.join(jarvis_base, f"profile_{slug}")
        os.makedirs(target_dir, exist_ok=True)
        return target_dir, matched["name"]

    slug = re.sub(r"[^a-z0-9_]", "_", query_clean)
    target_dir = os.path.join(jarvis_base, f"profile_{slug}")
    os.makedirs(target_dir, exist_ok=True)
    return target_dir, profile_query


def get_open_pages():
    """Returns all open, non-closed pages in the current browser context."""
    if state["context"] is None:
        return []
    try:
        pages = []
        for p in state["context"].pages:
            if not p.is_closed():
                pages.append(p)
        return pages
    except Exception:
        return []


async def ensure_browser(profile=None):
    """Ensures Playwright browser context is active, healthy, and on the requested profile."""
    req_dir, req_profile_name = _resolve_profile_dir(profile)

    if (
        state["context"] is not None
        and state["current_profile"] == req_profile_name
    ):
        pages = get_open_pages()
        if pages:
            # is_closed() is client-side bookkeeping and can be stale after the
            # browser dies out from under us — probe the real connection so a
            # dead browser triggers a relaunch instead of endless
            # "Target page has been closed" errors on every command.
            try:
                await asyncio.wait_for(pages[0].evaluate("1"), timeout=3)
                return
            except asyncio.TimeoutError:
                return  # slow page, but the connection is alive
            except Exception:
                print("[browser-daemon] browser state is stale (liveness probe failed); relaunching…", file=sys.stderr)
                state["context"] = None
        else:
            try:
                await state["context"].new_page()
                state["active_tab_index"] = 0
                return
            except Exception:
                state["context"] = None

    if state["context"] is not None:
        try:
            await state["context"].close()
        except Exception:
            pass
        state["context"] = None

    _clean_profile_locks(req_dir)

    if state["pw"] is None:
        state["pw"] = await async_playwright().start()

    launch_args = [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-infobars",
    ]

    # The profile dir remembers which engine last worked with it. A profile
    # created by Playwright's Chromium makes the real-Chrome channel launch
    # crash (SIGTRAP) — the window comes up on about:blank, the command dies,
    # and the user stares at a blank browser. Once Chromium is known to be
    # the working engine, skip the doomed Chrome attempt entirely.
    engine_marker = os.path.join(req_dir, ".jarvis_engine")
    preferred = None
    try:
        with open(engine_marker) as f:
            preferred = f.read().strip()
    except Exception:
        pass

    def _remember_engine(name):
        try:
            with open(engine_marker, "w") as f:
                f.write(name)
        except Exception:
            pass

    context = None
    if preferred != "chromium" and not state.get("force_chromium"):
        try:
            context = await state["pw"].chromium.launch_persistent_context(
                user_data_dir=req_dir,
                channel="chrome",
                headless=False,
                args=launch_args,
                no_viewport=True,
                ignore_https_errors=True,
            )
            _remember_engine("chrome")
        except Exception as e:
            print(f"[browser-daemon] Chrome channel launch failed ({e}); cleaning locks and trying Chromium…", file=sys.stderr)
            _remember_engine("chromium")
            _clean_profile_locks(req_dir)
    if context is None:
        try:
            context = await state["pw"].chromium.launch_persistent_context(
                user_data_dir=req_dir,
                headless=False,
                args=launch_args,
                no_viewport=True,
                ignore_https_errors=True,
            )
            _remember_engine("chromium")
        except Exception as e2:
            print(f"[browser-daemon] Persistent launch failed ({e2}); falling back to ephemeral launch…", file=sys.stderr)
            state["browser"] = await state["pw"].chromium.launch(
                headless=False,
                args=launch_args,
            )
            context = await state["browser"].new_context(no_viewport=True, ignore_https_errors=True)

    state["context"] = context
    state["current_profile"] = req_profile_name
    state["active_tab_index"] = 0

    pages = get_open_pages()
    if not pages:
        await context.new_page()


async def build_tab_list():
    """Returns structured metadata for all open tabs."""
    pages = get_open_pages()
    active_idx = max(0, min(state.get("active_tab_index", 0), len(pages) - 1)) if pages else 0
    tabs = []
    for i, p in enumerate(pages):
        try:
            title = await p.title() or "Untitled"
            url = p.url or "about:blank"
        except Exception:
            title, url = "Tab", ""
        tabs.append({
            "index": i,
            "title": title,
            "url": url,
            "is_active": (i == active_idx),
        })
    return tabs


def _first_pdf_for_auto_upload():
    """Picks the file to hand to an auto-intercepted file chooser: the consent
    template if present, else the first PDF found in the usual places."""
    consent = os.path.join(ROOT, "Informed_Consent Template.pdf")
    if os.path.isfile(consent):
        return consent
    for d in (ROOT, os.path.expanduser("~/Downloads"),
              os.path.expanduser("~/Documents"), os.path.expanduser("~")):
        try:
            pdfs = sorted(f for f in os.listdir(d) if f.lower().endswith(".pdf"))
        except OSError:
            continue
        if pdfs:
            return os.path.join(d, pdfs[0])
    return None


def _attach_auto_file_chooser(page):
    """Makes sure a file chooser opened by a plain click (outside the explicit
    upload_file flow) never blocks the page: Playwright intercepts it (so the
    native file manager never appears) and the first available PDF is selected."""
    if getattr(page, "_auto_chooser_attached", False):
        return

    async def _auto_choose(chooser):
        if state.get("expecting_file_chooser"):
            return
        pdf = _first_pdf_for_auto_upload()
        if not pdf:
            print("[browser-daemon] file chooser opened but no PDF found to auto-select", file=sys.stderr)
            return
        try:
            await chooser.set_files(pdf)
            print(f"[browser-daemon] auto-selected '{pdf}' in file chooser", file=sys.stderr)
        except Exception as e:
            print(f"[browser-daemon] auto file chooser selection failed: {e}", file=sys.stderr)

    page.on("filechooser", _auto_choose)
    page._auto_chooser_attached = True


async def get_active_page(tab_index=None, profile=None):
    """Retrieves the active page (or specific tab_index) and brings it to front."""
    await ensure_browser(profile=profile)
    pages = get_open_pages()
    if not pages:
        page = await state["context"].new_page()
        pages = [page]

    if tab_index is not None:
        try:
            idx = int(tab_index)
            if 0 <= idx < len(pages):
                state["active_tab_index"] = idx
            elif -len(pages) <= idx < 0:
                state["active_tab_index"] = len(pages) + idx
        except (ValueError, TypeError):
            pass

    idx = max(0, min(state.get("active_tab_index", 0), len(pages) - 1))
    state["active_tab_index"] = idx
    page = pages[idx]
    _attach_auto_file_chooser(page)
    try:
        await page.bring_to_front()
    except Exception:
        pass
    return page


# ============================================================================
# Flutter Web Automation Engine
# ============================================================================

async def is_flutter_page(page):
    """Detects if the active page is running Flutter Web (CanvasKit, Skwasm, or HTML renderer)."""
    try:
        return await page.evaluate("""() => {
            if (document.querySelector('flutter-view, flt-glass-pane, flt-semantics-host, flt-semantics-placeholder, flt-scene-host')) return true;
            if (window._flutter || window.flutterCanvasKit || window.flutterConfiguration) return true;
            const scripts = Array.from(document.querySelectorAll('script'));
            return scripts.some(s => (s.src || '').includes('flutter.js') || (s.src || '').includes('main.dart.js'));
        }""")
    except Exception:
        return False


async def enable_flutter_semantics(page):
    """Enables Flutter accessibility and semantics tree for full widget inspection."""
    try:
        already_live = await page.evaluate("""() => {
            const host = document.querySelector('flt-semantics-host');
            const alreadyLive = !!(host && host.childElementCount > 0);
            if (!alreadyLive) {
                const ph = document.querySelector('flt-semantics-placeholder, [role="button"][aria-label*="accessibility" i]');
                if (ph) {
                    ph.click();
                }
            }
            try {
                window.dispatchEvent(new Event('flutter-semantics-update'));
            } catch (e) {}
            if (host) {
                host.focus();
            }
            return alreadyLive;
        }""")
        # Full settle only on first activation; once the tree is live a short
        # refresh window is enough between actions.
        await page.wait_for_timeout(100 if already_live else 350)
    except Exception:
        pass


async def extract_flutter_widgets(page):
    """Extracts interactive Flutter widgets from semantics tree and canvas coordinates."""
    await enable_flutter_semantics(page)
    try:
        return await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const nodes = document.querySelectorAll(
                'flt-semantics, [role="button"], [role="link"], [role="textbox"], [role="checkbox"], [role="switch"], [role="tab"], [role="heading"], [aria-label], [placeholder], input, button, select, textarea'
            );
            
            for (const n of nodes) {
                const rect = n.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0 || window.getComputedStyle(n).visibility === 'hidden') continue;
                
                const label = (n.getAttribute('aria-label') || n.innerText || n.getAttribute('placeholder') || n.getAttribute('title') || '').trim().replace(/\\s+/g, ' ');
                const role = n.getAttribute('role') || (n.tagName.toLowerCase() === 'input' ? 'textbox' : (n.tagName.toLowerCase() === 'button' ? 'button' : 'widget'));
                const value = n.getAttribute('aria-valuenow') || n.value || (n.getAttribute('aria-checked') === 'true' ? 'checked' : (n.getAttribute('aria-checked') === 'false' ? 'unchecked' : ''));
                
                if (!label && role === 'widget' && !value) continue;
                
                const key = `${role}:${label}:${Math.round(rect.x)}:${Math.round(rect.y)}`;
                if (seen.has(key)) continue;
                seen.add(key);
                
                const center_x = Math.round(rect.x + rect.width / 2);
                const center_y = Math.round(rect.y + rect.height / 2);
                
                results.push({
                    role: role,
                    label: label ? label.slice(0, 80) : undefined,
                    value: value || undefined,
                    bounds: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        center_x: center_x,
                        center_y: center_y,
                    },
                    selector_hint: label ? `flutter:label("${label.slice(0, 30)}")` : `flutter:coords(${center_x},${center_y})`
                });
                if (results.length >= 250) break;
            }
            return results;
        }""")
    except Exception as e:
        print(f"[browser-daemon] extract_flutter_widgets error: {e}", file=sys.stderr)
        return []


def _slim_widgets(widgets):
    """Compact per-widget shape sent back to the model: role/label/value plus
    center coordinates only. The full bounds + selector_hint form roughly
    doubled the JSON the LLM has to read every round — on a 250-widget Novatek
    form that alone made each model turn several seconds slower."""
    slim = []
    for w in widgets:
        b = w.get("bounds") or {}
        item = {"role": w.get("role"), "x": b.get("center_x"), "y": b.get("center_y")}
        if w.get("label"):
            item["label"] = w["label"]
        if w.get("value"):
            item["value"] = w["value"]
        slim.append(item)
    return slim


async def find_flutter_widget_coords(page, target):
    """Finds exact viewport (center_x, center_y) coordinates for a Flutter widget target.

    Semantics enabling happens inside extract_flutter_widgets; the coords()
    fast path below needs no semantics at all.
    """
    target_clean = str(target or "").strip()

    coord_match = re.search(r"coords?\(?(\d+)[,\s]+(\d+)\)?", target_clean, re.IGNORECASE)
    if coord_match:
        return int(coord_match.group(1)), int(coord_match.group(2)), f"coords({coord_match.group(1)},{coord_match.group(2)})"

    label_query = target_clean
    if target_clean.startswith(("flutter:label(", "flutter:text(", "text=")):
        label_query = target_clean.split("(", 1)[-1].rstrip(")").strip("\"'")

    widgets = await extract_flutter_widgets(page)

    # 1. Exact match (case-insensitive)
    for w in widgets:
        w_label = (w.get("label") or "").strip().lower()
        if w_label and w_label == label_query.lower():
            b = w["bounds"]
            return b["center_x"], b["center_y"], w.get("label", "")

    # 2. Interactive widgets substring match (buttons, textboxes, links, etc.)
    for w in widgets:
        w_label = (w.get("label") or "").strip().lower()
        role = (w.get("role") or "").lower()
        if w_label and role in ("button", "textbox", "checkbox", "switch", "tab", "link", "combobox", "option"):
            if label_query.lower() in w_label or w_label in label_query.lower():
                b = w["bounds"]
                return b["center_x"], b["center_y"], w.get("label", "")

    # 3. General substring match
    for w in widgets:
        w_label = (w.get("label") or "").strip().lower()
        if w_label and (label_query.lower() in w_label or w_label in label_query.lower()):
            b = w["bounds"]
            return b["center_x"], b["center_y"], w.get("label", "")

    # 4. Role/type match
    if "=" in target_clean:
        attr, val = target_clean.split("=", 1)
        attr = attr.strip().lower()
        val = val.strip().lower().strip("\"'")
        for w in widgets:
            if attr in ("role", "type") and (w.get("role") or "").lower() == val:
                b = w["bounds"]
                return b["center_x"], b["center_y"], f"{w.get('role')}:{w.get('label')}"

    return None, None, None


# ============================================================================
# Command Handlers
# ============================================================================

async def cmd_detect_app_type(payload):
    """Detects whether the active page is Flutter Web or standard DOM web application."""
    tab_index = payload.get("tab_index")
    page = await get_active_page(tab_index=tab_index)

    is_flt = await is_flutter_page(page)
    if not is_flt:
        try:
            await page.wait_for_function("""() => {
                return !!(document.querySelector('flutter-view, flt-glass-pane, flt-semantics-host, flt-semantics-placeholder') || window._flutter || window.flutterCanvasKit);
            }""", timeout=2500)
            is_flt = True
        except Exception:
            pass

    widgets = []
    if is_flt:
        await enable_flutter_semantics(page)
        widgets = await extract_flutter_widgets(page)

    title = await page.title()
    return {
        "app_type": "flutter_web" if is_flt else "standard_web",
        "is_flutter": is_flt,
        "renderer": "canvaskit_or_html" if is_flt else "html_dom",
        "semantics_enabled": bool(is_flt),
        "flutter_widgets_count": len(widgets),
        "url": page.url,
        "title": title,
        "recommended_tools": (
            ["browser_flutter_click", "browser_flutter_type", "browser_flutter_get_widgets"]
            if is_flt else
            ["browser_click", "browser_type", "browser_get_content"]
        ),
        "flutter_widgets_preview": widgets[:10] if is_flt else [],
    }


async def cmd_flutter_widgets(payload):
    """Extracts Flutter widgets from semantics tree and canvas coordinates."""
    tab_index = payload.get("tab_index")
    page = await get_active_page(tab_index=tab_index)
    widgets = await extract_flutter_widgets(page)
    title = await page.title()
    return {
        "url": page.url,
        "title": title,
        "total_widgets": len(widgets),
        "active_tab_index": state["active_tab_index"],
        "coordinate_usage": "click/type with target 'flutter:coords(x,y)'",
        "widgets": widgets if payload.get("verbose") else _slim_widgets(widgets),
    }


async def cmd_flutter_click(payload):
    """Clicks a Flutter widget on the CanvasKit canvas using semantics and coordinate dispatch."""
    target = payload.get("target") or payload.get("selector") or payload.get("label") or ""
    tab_index = payload.get("tab_index")
    double_click = bool(payload.get("double_click", False))

    page = await get_active_page(tab_index=tab_index)
    cx, cy, matched_label = await find_flutter_widget_coords(page, target)

    if cx is None or cy is None:
        avail = await extract_flutter_widgets(page)
        return {
            "error": f"Flutter widget '{target}' not found. Try inspecting available widgets with browser_flutter_get_widgets.",
            "available_widgets": avail[:10],
        }

    if double_click:
        await page.mouse.dblclick(cx, cy)
    else:
        await page.mouse.click(cx, cy)

    await page.wait_for_timeout(250)
    title = await page.title()
    return {
        "status": "flutter_clicked",
        "target": target,
        "matched_label": matched_label,
        "coordinates": {"x": cx, "y": cy},
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_flutter_type(payload):
    """Types text into a Flutter text field on the CanvasKit canvas."""
    target = payload.get("target") or payload.get("selector") or payload.get("label") or ""
    text = str(payload.get("text", ""))
    press_enter = bool(payload.get("press_enter", False))
    clear = bool(payload.get("clear", True))
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)
    cx, cy, matched_label = await find_flutter_widget_coords(page, target)

    if cx is None or cy is None:
        avail = await extract_flutter_widgets(page)
        return {
            "error": f"Flutter text field '{target}' not found. Try inspecting available widgets with browser_flutter_get_widgets.",
            "available_widgets": avail[:10],
        }

    await page.mouse.click(cx, cy)
    await page.wait_for_timeout(150)

    if clear:
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")

    await page.keyboard.type(text, delay=12)
    if press_enter:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(350)

    title = await page.title()
    return {
        "status": "flutter_typed",
        "target": target,
        "matched_label": matched_label,
        "text": text,
        "coordinates": {"x": cx, "y": cy},
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_flutter_run_test(payload):
    """Runs a Patrol test, Flutter integration test, or Flutter driver command."""
    test_type = (payload.get("test_type") or "integration_test").lower()
    target = (payload.get("target") or "").strip()
    device = (payload.get("device") or "chrome").strip()
    project_path = (payload.get("project_path") or ROOT).strip()
    extra_args = payload.get("extra_args") or []

    binaries = _find_flutter_binaries()
    flutter_bin = binaries.get("flutter", "flutter")
    patrol_bin = binaries.get("patrol")

    cmd = []
    if test_type == "patrol":
        if patrol_bin:
            cmd = [patrol_bin, "test", "-d", device]
        else:
            cmd = [flutter_bin, "test", "-d", device]
    elif test_type == "flutter_drive":
        cmd = [flutter_bin, "drive", "-d", device]
    else:
        cmd = [flutter_bin, "test", "-d", device]

    if target:
        cmd.append(target)
    if extra_args:
        cmd.extend(extra_args)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=180)
        stdout_text = stdout_bytes.decode("utf-8", errors="ignore").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="ignore").strip()
        return {
            "status": "success" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "command": " ".join(cmd),
            "stdout": stdout_text[-3000:],
            "stderr": stderr_text[-1000:],
        }
    except asyncio.TimeoutError:
        return {"error": f"Flutter test command timed out after 180s: {' '.join(cmd)}"}
    except Exception as e:
        return {"error": f"Failed to execute Flutter test ({e}): {' '.join(cmd)}"}


async def cmd_list_profiles(_payload):
    """Lists all available Chrome profiles on the host."""
    profiles = get_chrome_profiles()
    return {
        "active_profile": state.get("current_profile", "default"),
        "total_profiles": len(profiles),
        "profiles": profiles,
    }


async def cmd_open(payload):
    """Opens a URL, self-healing once if the browser died mid-command (a
    crashed first launch used to leave the user staring at a blank window
    until they asked again — now the relaunch and navigation happen here)."""
    try:
        return await _cmd_open_inner(payload)
    except Exception as e:
        low = str(e).lower()
        if payload.get("_retried") or ("closed" not in low and "crash" not in low):
            raise
        print(f"[browser-daemon] /open hit a dead browser ({e}); relaunching and retrying…", file=sys.stderr)
        state["context"] = None
        state["force_chromium"] = True  # the crashed engine doesn't get a second try this session
        payload = dict(payload)
        payload["_retried"] = True
        return await cmd_open(payload)


async def _cmd_open_inner(payload):
    """Opens a URL, navigating in the active tab or opening a new tab."""
    url = (payload.get("url") or "").strip()
    profile = payload.get("profile")
    new_tab = bool(payload.get("new_tab", False))
    tab_index = payload.get("tab_index")

    if not url:
        url = "https://www.google.com"
    elif not url.startswith(("http://", "https://", "file://", "about:", "chrome://")):
        url = "https://" + url

    await ensure_browser(profile=profile)
    pages = get_open_pages()

    if new_tab:
        page = await state["context"].new_page()
        pages = get_open_pages()
        state["active_tab_index"] = len(pages) - 1
    else:
        if tab_index is not None:
            page = await get_active_page(tab_index=tab_index)
        elif len(pages) == 1 and pages[0].url in ("about:blank", "chrome://newtab/"):
            page = pages[0]
            state["active_tab_index"] = 0
        else:
            page = await get_active_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=55000)
    except PlaywrightTimeoutError:
        # If domcontentloaded timed out due to background network or Cloudflare, check if the page actually loaded
        pass
    except Exception:
        try:
            await page.goto(url, wait_until="commit", timeout=15000)
        except Exception:
            pass

    await page.bring_to_front()

    is_flt = await is_flutter_page(page)
    if not is_flt:
        try:
            await page.wait_for_function("""() => {
                return !!(document.querySelector('flutter-view, flt-glass-pane, flt-semantics-host, flt-semantics-placeholder') || window._flutter || window.flutterCanvasKit);
            }""", timeout=3000)
            is_flt = True
        except Exception:
            pass

    if is_flt:
        await enable_flutter_semantics(page)

    all_tabs = await build_tab_list()
    title = await page.title()
    return {
        "status": "opened",
        "url": page.url,
        "title": title,
        "app_type": "flutter_web" if is_flt else "standard_web",
        "profile": state.get("current_profile", "default"),
        "active_tab_index": state["active_tab_index"],
        "total_tabs": len(all_tabs),
        "tabs": all_tabs,
    }


async def cmd_list(_payload):
    """Lists all open tabs in the controlled browser window."""
    if state["context"] is None:
        return {
            "status": "no_browser_open",
            "total_tabs": 0,
            "active_tab_index": None,
            "profile": None,
            "tabs": [],
        }

    tabs = await build_tab_list()
    return {
        "status": "ok",
        "total_tabs": len(tabs),
        "active_tab_index": state.get("active_tab_index", 0),
        "profile": state.get("current_profile", "default"),
        "tabs": tabs,
    }


async def cmd_switch_tab(payload):
    """Switches the active tab by tab index or matching title/URL query."""
    if state["context"] is None:
        return {"error": "No browser is currently open."}

    pages = get_open_pages()
    if not pages:
        return {"error": "No open tabs to switch to."}

    target_idx = None
    if "tab_index" in payload and payload["tab_index"] is not None:
        try:
            idx = int(payload["tab_index"])
            if 0 <= idx < len(pages):
                target_idx = idx
            elif -len(pages) <= idx < 0:
                target_idx = len(pages) + idx
        except (ValueError, TypeError):
            pass

    query = (payload.get("query") or "").strip().lower()
    if target_idx is None and query:
        for i, p in enumerate(pages):
            try:
                p_title = await p.title() or ""
                p_url = p.url or ""
                if query in p_title.lower() or query in p_url.lower():
                    target_idx = i
                    break
            except Exception:
                pass

    if target_idx is None:
        all_tabs = await build_tab_list()
        return {
            "error": f"Tab not found matching {payload}. Total open tabs: {len(pages)}.",
            "tabs": all_tabs,
        }

    page = await get_active_page(tab_index=target_idx)
    await page.bring_to_front()

    tabs = await build_tab_list()
    title = await page.title()
    return {
        "status": "switched",
        "active_tab_index": state["active_tab_index"],
        "title": title,
        "url": page.url,
        "total_tabs": len(tabs),
        "tabs": tabs,
    }


async def cmd_close(payload):
    """Closes tabs or the entire browser window cleanly."""
    scope = (payload.get("scope") or "tab").lower()
    tab_index = payload.get("tab_index")

    if state["context"] is None:
        _clean_orphaned_playwright_processes()
        return {
            "status": "nothing_open",
            "message": "No browser was open.",
            "remaining_tabs_count": 0,
            "tabs": [],
        }

    pages = get_open_pages()

    if scope in ("all", "browser") or not pages:
        for p in pages:
            try:
                await p.close()
            except Exception:
                pass
        try:
            await state["context"].close()
        except Exception:
            pass
        if state["browser"] is not None:
            try:
                await state["browser"].close()
            except Exception:
                pass

        state["context"] = None
        state["browser"] = None
        state["current_profile"] = None
        state["active_tab_index"] = 0
        _clean_orphaned_playwright_processes()

        return {
            "status": "browser_closed",
            "message": "All browser tabs and windows have been closed.",
            "remaining_tabs_count": 0,
            "tabs": [],
        }

    if scope == "others":
        active_p = await get_active_page()
        for p in pages:
            if p != active_p:
                try:
                    await p.close()
                except Exception:
                    pass
        state["active_tab_index"] = 0
        remaining = get_open_pages()
        all_tabs = await build_tab_list()
        return {
            "status": "other_tabs_closed",
            "message": "Closed all tabs except the active tab.",
            "remaining_tabs_count": len(remaining),
            "active_tab_index": 0,
            "tabs": all_tabs,
        }

    target_page = None
    if tab_index is not None:
        try:
            idx = int(tab_index)
            if 0 <= idx < len(pages):
                target_page = pages[idx]
        except (ValueError, TypeError):
            pass

    if target_page is None:
        target_page = await get_active_page()

    closed_title = ""
    closed_url = ""
    try:
        closed_title = await target_page.title()
        closed_url = target_page.url
        await target_page.close()
    except Exception:
        pass

    remaining = get_open_pages()
    if not remaining:
        try:
            await state["context"].close()
        except Exception:
            pass
        if state["browser"] is not None:
            try:
                await state["browser"].close()
            except Exception:
                pass
        state["context"] = None
        state["browser"] = None
        state["current_profile"] = None
        state["active_tab_index"] = 0
        _clean_orphaned_playwright_processes()
        return {
            "status": "browser_closed",
            "message": f"Closed '{closed_title}'. That was the last tab, so the browser window closed.",
            "remaining_tabs_count": 0,
            "tabs": [],
        }

    state["active_tab_index"] = min(state.get("active_tab_index", 0), len(remaining) - 1)
    try:
        await remaining[state["active_tab_index"]].bring_to_front()
    except Exception:
        pass

    tabs = await build_tab_list()
    return {
        "status": "tab_closed",
        "closed_tab": {"title": closed_title, "url": closed_url},
        "remaining_tabs_count": len(tabs),
        "active_tab_index": state["active_tab_index"],
        "tabs": tabs,
    }


async def cmd_click(payload):
    """Clicks an element, automatically detecting Flutter Web vs Standard DOM."""
    selector = (payload.get("selector") or "").strip()
    if not selector:
        return {"error": "no selector provided"}
    timeout_ms = int(payload.get("timeout_ms", 8000))
    double_click = bool(payload.get("double_click", False))
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)

    if selector.startswith(("flutter:", "coords:")) or (await is_flutter_page(page)):
        cx, cy, matched_label = await find_flutter_widget_coords(page, selector)
        if cx is not None and cy is not None:
            if double_click:
                await page.mouse.dblclick(cx, cy)
            else:
                await page.mouse.click(cx, cy)
            await page.wait_for_timeout(200)
            title = await page.title()
            return {
                "status": "clicked",
                "engine": "flutter_web",
                "selector": selector,
                "matched_label": matched_label,
                "coordinates": {"x": cx, "y": cy},
                "url": page.url,
                "title": title,
                "active_tab_index": state["active_tab_index"],
            }

    loc = page.locator(selector).first
    await loc.scroll_into_view_if_needed(timeout=timeout_ms)
    if double_click:
        await loc.dblclick(timeout=timeout_ms)
    else:
        await loc.click(timeout=timeout_ms)
    await page.wait_for_timeout(200)

    title = await page.title()
    return {
        "status": "clicked",
        "engine": "standard_dom",
        "selector": selector,
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_type(payload):
    """Types text into an input or textarea, auto-adapting to Flutter Web or Standard DOM."""
    selector = (payload.get("selector") or "").strip()
    if not selector:
        return {"error": "no selector provided"}
    text = str(payload.get("text", ""))
    press_enter = bool(payload.get("press_enter", False))
    clear = bool(payload.get("clear", True))
    timeout_ms = int(payload.get("timeout_ms", 8000))
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)

    if selector.startswith(("flutter:", "coords:")) or (await is_flutter_page(page)):
        cx, cy, matched_label = await find_flutter_widget_coords(page, selector)
        if cx is not None and cy is not None:
            await page.mouse.click(cx, cy)
            await page.wait_for_timeout(150)
            if clear:
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(text, delay=12)
            if press_enter:
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(300)
            title = await page.title()
            return {
                "status": "typed",
                "engine": "flutter_web",
                "selector": selector,
                "matched_label": matched_label,
                "text": text,
                "coordinates": {"x": cx, "y": cy},
                "url": page.url,
                "title": title,
                "active_tab_index": state["active_tab_index"],
            }

    loc = page.locator(selector).first
    await loc.scroll_into_view_if_needed(timeout=timeout_ms)
    if clear:
        await loc.fill(text, timeout=timeout_ms)
    else:
        await loc.type(text, timeout=timeout_ms)
    if press_enter:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)

    title = await page.title()
    return {
        "status": "typed",
        "engine": "standard_dom",
        "selector": selector,
        "text": text,
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_press_key(payload):
    """Presses a keyboard key on the active page."""
    key = (payload.get("key") or "Enter").strip()
    selector = (payload.get("selector") or "").strip()
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)
    if selector:
        if await is_flutter_page(page):
            cx, cy, _ = await find_flutter_widget_coords(page, selector)
            if cx is not None and cy is not None:
                await page.mouse.click(cx, cy)
        else:
            await page.locator(selector).first.focus()

    await page.keyboard.press(key)
    await page.wait_for_timeout(200)

    title = await page.title()
    return {
        "status": "pressed",
        "key": key,
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_scroll(payload):
    """Scrolls the page or scrolls a selector into view, auto-handling Flutter canvas scrolling."""
    direction = (payload.get("direction") or "down").lower()
    amount = int(payload.get("amount", 500))
    selector = (payload.get("selector") or "").strip()
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)
    is_flt = await is_flutter_page(page)

    vp = page.viewport_size or {"width": 1280, "height": 800}
    # On Flutter split layouts (like Novatek), the form is located on the right side (~75% width, 50% height)
    target_x = int(vp["width"] * 0.75)
    target_y = int(vp["height"] * 0.5)

    if selector:
        if is_flt:
            cx, cy, _ = await find_flutter_widget_coords(page, selector)
            if cx is not None and cy is not None:
                target_x, target_y = cx, cy
        else:
            try:
                await page.locator(selector).first.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass

    delta_y = amount if direction == "down" else (-amount if direction == "up" else 0)

    async def smooth_wheel(total_delta):
        # One big wheel delta makes the page jump/teleport (and on Flutter the
        # old synthetic touch drag added fling momentum on top, overshooting
        # past questions). Feed the same distance as a stream of small wheel
        # ticks instead — the scroll animates smoothly and lands exactly on
        # `total_delta`, so nothing gets skipped past.
        step = 60 if total_delta > 0 else -60
        remaining = total_delta
        while abs(remaining) > 0:
            tick = step if abs(remaining) >= abs(step) else remaining
            await page.mouse.wheel(0, tick)
            remaining -= tick
            await page.wait_for_timeout(30)
        # Let any scroll animation settle before the caller re-reads widgets.
        await page.wait_for_timeout(250)

    if is_flt:
        # Move mouse over the target scrollable container
        await page.mouse.move(target_x, target_y)
        await page.wait_for_timeout(50)

        if delta_y != 0:
            await smooth_wheel(delta_y)

        if direction == "top":
            await page.keyboard.press("Home")
            await smooth_wheel(-3000)
        elif direction == "bottom":
            await page.keyboard.press("End")
            await smooth_wheel(3000)

        # Refresh semantics tree so newly revealed widgets are indexed
        await enable_flutter_semantics(page)
    else:
        if delta_y != 0:
            await smooth_wheel(delta_y)

        if direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    title = await page.title()
    return {
        "status": "scrolled",
        "direction": direction,
        "amount": amount,
        "engine": "flutter_targeted" if is_flt else "standard_dom",
        "target_coordinates": {"x": target_x, "y": target_y} if is_flt else None,
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_get_content(payload):
    """Extracts text, interactive DOM elements, or Flutter widgets from the active page."""
    mode = payload.get("mode", "text")
    max_chars = int(payload.get("max_chars", 4000))
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)
    is_flt = await is_flutter_page(page)
    title = await page.title()

    if mode == "flutter_widgets" or (is_flt and mode == "elements"):
        flutter_widgets = await extract_flutter_widgets(page)
        return {
            "app_type": "flutter_web",
            "url": page.url,
            "title": title,
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "elements": _slim_widgets(flutter_widgets),
            "flutter_widgets_count": len(flutter_widgets),
        }
    elif mode == "elements":
        elements = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const list = document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="link"], h1, h2, h3');
            for (const el of list) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0 || window.getComputedStyle(el).visibility === 'hidden') continue;
                const tag = el.tagName.toLowerCase();
                const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || '').trim().replace(/\\s+/g, ' ');
                const id = el.id ? '#' + el.id : '';
                const name = el.name ? `[name="${el.name}"]` : '';
                const placeholder = el.placeholder || '';
                const key = `${tag}:${id}:${name}:${text.slice(0, 40)}`;
                if (seen.has(key)) continue;
                seen.add(key);
                results.push({
                    tag,
                    id: el.id || undefined,
                    name: el.name || undefined,
                    text: text ? text.slice(0, 80) : undefined,
                    placeholder: placeholder || undefined,
                    type: el.type || undefined,
                    selector_hint: id || name || (text ? `${tag}:has-text("${text.slice(0, 25).replace(/"/g, '')}")` : tag)
                });
                if (results.length >= 60) break;
            }
            return results;
        }""")
        return {
            "app_type": "standard_web",
            "url": page.url,
            "title": title,
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "elements": elements,
        }
    elif mode == "html":
        html = await page.content()
        return {
            "app_type": "flutter_web" if is_flt else "standard_web",
            "url": page.url,
            "title": title,
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "content": html[:max_chars],
        }
    else:
        if is_flt:
            widgets = await extract_flutter_widgets(page)
            labels = [w.get("label") for w in widgets if w.get("label")]
            text = "\n".join(labels) if labels else await page.evaluate("() => document.body ? document.body.innerText : ''")
        else:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        clean_text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return {
            "app_type": "flutter_web" if is_flt else "standard_web",
            "url": page.url,
            "title": title,
            "active_tab_index": state["active_tab_index"],
            "total_tabs": len(get_open_pages()),
            "content": clean_text[:max_chars],
        }


async def cmd_screenshot(payload):
    """Captures a screenshot of the visible screen or full page."""
    full_page = bool(payload.get("full_page", False))
    save_path = payload.get("save_path")
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)

    if not save_path:
        captures_dir = os.path.join(ROOT, "notes", "captures")
        os.makedirs(captures_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(captures_dir, f"browser_{ts}.png")
    else:
        save_path = os.path.expanduser(save_path)
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

    await page.screenshot(path=save_path, full_page=full_page)
    filename = os.path.basename(save_path)
    title = await page.title()
    return {
        "status": "screenshot_saved",
        "path": save_path,
        "filename": filename,
        "screenshot_url": f"/captures/{filename}",
        "url": page.url,
        "title": title,
        "active_tab_index": state["active_tab_index"],
    }


async def cmd_upload_file(payload):
    """Uploads a local file by clicking a file picker button/input or setting input files on Flutter/DOM."""
    file_path = (payload.get("file_path") or "").strip()
    selector = (payload.get("selector") or payload.get("target") or "").strip()
    tab_index = payload.get("tab_index")
    timeout_ms = int(payload.get("timeout_ms", 10000))

    if not file_path:
        return {"error": "no file_path provided"}

    file_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}

    page = await get_active_page(tab_index=tab_index)

    state["expecting_file_chooser"] = True
    try:
        # If active page is Flutter Web or selector indicates Flutter
        if (await is_flutter_page(page)):
            await enable_flutter_semantics(page)
            # 1. If explicit selector given, try finding coordinates
            candidates = [selector] if selector else []
            if not candidates:
                # Discover upload/file buttons from semantics tree
                widgets = await extract_flutter_widgets(page)
                for w in widgets:
                    lbl = (w.get("label") or "").lower()
                    # Whole words only. Substring matching put the navbar's
                    # "View Profile PARTICIPANT ID …" chip in this list, because
                    # "file" is inside "Profile" — clicking it opened the
                    # participant profile dialog instead of a file chooser, over
                    # and over.
                    if not _UPLOAD_KEYWORD_RE.search(lbl):
                        continue
                    if _autopilot_click_forbidden(w):
                        continue
                    candidates.append(w.get("label"))

            for target in candidates:
                if not target:
                    continue
                cx, cy, matched_label = await find_flutter_widget_coords(page, target)
                # Never let an upload click land on page furniture. This path
                # clicks with page.mouse.click directly, so the guard in
                # _autopilot_click does not cover it — and the participant chip
                # was reaching it (see _UPLOAD_KEYWORD_RE) and opening a profile
                # dialog in a loop.
                if cx is not None and cy is not None and _autopilot_click_forbidden(
                        {"label": matched_label or target, "bounds": {"center_y": cy}}):
                    print(f"[browser-daemon] upload: refusing target {str(target)[:40]!r}",
                          file=sys.stderr)
                    continue
                if cx is not None and cy is not None:
                    try:
                        async with page.expect_file_chooser(timeout=3500) as fc_info:
                            await page.mouse.click(cx, cy)
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(file_path)
                        await page.wait_for_timeout(500)
                        return {
                            "status": "file_uploaded",
                            "engine": "flutter_file_chooser",
                            "file_path": file_path,
                            "target": target,
                            "matched_label": matched_label,
                            "coordinates": {"x": cx, "y": cy},
                        }
                    except Exception:
                        pass

        # Check for HTML file input
        input_file = page.locator('input[type="file"]').first
        if await input_file.count() > 0:
            try:
                await input_file.set_input_files(file_path)
                return {
                    "status": "file_uploaded",
                    "engine": "input_file_direct",
                    "file_path": file_path,
                }
            except Exception:
                pass

        # Standard DOM selector click with file chooser
        if selector:
            try:
                loc = page.locator(selector).first
                async with page.expect_file_chooser(timeout=3000) as fc_info:
                    await loc.click(timeout=timeout_ms)
                file_chooser = await fc_info.value
                await file_chooser.set_files(file_path)
                await page.wait_for_timeout(500)
                return {
                    "status": "file_uploaded",
                    "engine": "dom_file_chooser",
                    "file_path": file_path,
                    "selector": selector,
                }
            except Exception:
                try:
                    await page.set_input_files(selector, file_path, timeout=timeout_ms)
                    return {
                        "status": "file_uploaded",
                        "engine": "set_input_files",
                        "file_path": file_path,
                        "selector": selector,
                    }
                except Exception:
                    pass

        return {
            "status": "file_selected",
            "file_path": file_path,
            "message": f"Prepared file {file_path} for upload.",
        }
    except Exception as e:
        return {"error": f"Failed to upload file '{file_path}': {e}"}
    finally:
        state["expecting_file_chooser"] = False


# ============================================================================
# Novatek Visit-Mode Form Autopilot
# ============================================================================
# Fills and submits forms with zero LLM involvement — the whole
# answer/scroll/submit loop runs in-process at machine speed. Scope is
# deliberately hard-limited to the VISIT MODE screen (the one titled
# "Visit Mode" with the sidebar Forms list and the N/M progress counter).
# cmd_takeover_participant chains whole visits from the participant profile.

# Question markers: "1.", "4-1." (sub-questions).
_AUTOPILOT_MARKER_RE = re.compile(r"^\d+(-\d+)?\.$")
_AUTOPILOT_SIDEBAR_RESERVED = (
    "visit note", "discard", "early termination", "hold visit", "end visit",
)
_AUTOPILOT_NUMBER_HINTS = (
    "ranges", "number", "score", "count", "level", "dose", "age", "weight",
    "height", "temperature", "pulse", "systolic", "diastolic", "rate", "bpm",
    "mmhg", "(0-", "scale", "duration", "quantity", "spo2", "sp02",
    "pressure", "how many", "how much",
)
#: Checked BEFORE _AUTOPILOT_NUMBER_HINTS, because a question can contain a
#: quantity word and still want prose. "Who Administered the Dose?" matched
#: "dose" and was answered with 55 — into a field that accepts letters only, so
#: every candidate in the numeric ladder was guaranteed to be refused.
_AUTOPILOT_TEXT_HINTS = (
    "who ", "whom", "name", "personnel", "staff", "nurse", "physician",
    "doctor", "investigator", "initials", "signature", "comment", "describe",
    "description", "reason", "notes", "specify", "explain", "other (",
    "title", "site", "email", "address",
)

_AUTOPILOT_DIALOG_ACCEPT = ("ok", "confirm", "done", "set", "save", "select", "apply", "yes")
# Inline validation messages Novatek renders under a rejected field.
#: Substrings that identify an inline validation message. Deliberately phrases
#: rather than single words: a bare "required" or "between" also occurs in
#: ordinary question text ("Select a value between 1 and 10"), and a false
#: positive here makes the autopilot retype a perfectly good answer forever.
_AUTOPILOT_ERROR_HINTS = (
    "valid number", "invalid", "must be", "is required", "required field",
    "field is required", "please enter", "please select", "please provide",
    "cannot be empty", "can't be empty", "not a valid", "enter a valid",
    "out of range", "too long", "too short", "does not match", "no more than",
)


#: Novatek writes real typography in question titles — "SpO₂" uses U+2082, a
#: subscript two, so a plain "spo2" hint never matched and the question fell
#: through to the free-text ladder, typing "test" into a numeric field.
_DIGIT_LOOKALIKES = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")


def _normalize_hint(text):
    """Lowercased text with subscript/superscript digits folded to ASCII."""
    return (text or "").lower().translate(_DIGIT_LOOKALIKES)


#: A "Ranges:" chip, e.g. "36.5 - 37.5" or "95 - 100". Novatek shows the valid
#: bands beside every numeric question, which is a far better source for an
#: answer than a fixed ladder: the temperature field accepts 36.5-37.5 only, and
#: none of [55, 1, 10, 0, 100] is inside it.
_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[-\u2013\u2014]\s*(\d+(?:\.\d+)?)\s*$")


def _autopilot_range_value(blk):
    """A value inside one of the range chips this question displays, or None.

    Any in-range value satisfies the validator; clinical plausibility is not the
    goal for test data. The last chip is used because it is a stable choice —
    the bands are not ordered normal-first in any consistent way across
    questions (SpO2 puts the healthy band last, respiration rate second).
    """
    ranges = []
    for w in blk["members"]:
        match = _RANGE_RE.match((w.get("label") or "").strip())
        if match:
            low, high = float(match.group(1)), float(match.group(2))
            if high >= low:
                ranges.append((low, high))
    if not ranges:
        return None
    low, high = ranges[-1]
    middle = (low + high) / 2
    whole = round(middle)
    return str(int(whole)) if low <= whole <= high else str(round(middle, 1))


def _is_submit_label(label):
    """True for the form's submit button, whatever it is called.

    Prefix-matched, not equality: the question forms label it "Submit" but the
    entry-table forms (Concomitant Medications) label it "Submit Form". An
    exact test found the first and not the second, so the autopilot walked to
    the bottom of that form and reported "reached the bottom without finding
    Submit" while the button was on screen the whole time.
    """
    return (label or "").strip().lower().startswith("submit")


def _is_sign_label(label):
    """True for the signature button, whatever Novatek calls it today.

    Matched on a prefix rather than equality: the real buttons are "Sign Now"
    on the question and "Sign Document" in the dialog, and an exact "sign"
    test missed both — the question button was then clicked as if it were an
    ordinary answer option, which opened the credentials dialog and left it
    hanging, and a hanging modal blocks every field under it.
    """
    return (label or "").strip().lower().startswith("sign")


#: Screen-level messages that are NOT about any one question. The visit's own
#: "Actual visit date is required" banner sits inside the question panel and
#: was being attributed to whichever block it fell between — sending the
#: autopilot through 20+ variants of a date that had never been wrong.
_AUTOPILOT_SCREEN_LEVEL = ("actual visit date", "visit mode")


def _block_error_text(blk):
    """The inline validation message shown on this block, or "" if none."""
    for w in blk["members"]:
        label = (w.get("label") or "").strip()
        low = label.lower()
        if any(sl in low for sl in _AUTOPILOT_SCREEN_LEVEL):
            continue
        if any(h in low for h in _AUTOPILOT_ERROR_HINTS):
            return label
    return ""


#: Any date-looking text, used to recognise a field that is already set even
#: when the semantics tree does not expose its value.
_DATE_TEXT_RE = re.compile(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}")


def _visit_is_complete(progress):
    """True only when the progress counter reads N/N with N > 0.

    The single gate on ending a visit. Anything unreadable, malformed or short
    of full is not complete — ending a visit is irreversible, so this errs
    towards not doing it.
    """
    if not progress or progress.count("/") != 1:
        return False
    try:
        done, total = (int(x) for x in progress.split("/"))
    except (ValueError, TypeError):
        return False
    return total > 0 and done == total


#: The confirmation that follows End Visit. Novatek raises a "Continue
#: participation" dialog and the visit is not ended until it is answered — so
#: clicking End Visit alone leaves the run halted on a modal.
_CONTINUE_LABELS = ("continue", "continue participation", "confirm", "yes", "ok", "proceed")


def _end_visit_button(widgets):
    """The End Visit control, or None.

    Matched on `contains`: the accessible name is
    "You can end the visit only when all forms are completed. End Visit" — the
    helper text comes first, so a startswith test never found it.
    """
    return next((w for w in widgets if w.get("role") == "button"
                 and "end visit" in (w.get("label") or "").strip().lower()), None)


def _continue_button(widgets):
    """The button that confirms the Continue participation dialog, or None.

    Prefers the most specific label present rather than the first plausible
    one, so a bare "Cancel"-adjacent "OK" cannot win over "Continue".
    """
    buttons = [w for w in widgets if w.get("role") == "button" and w.get("label")]
    for wanted in _CONTINUE_LABELS:
        for w in buttons:
            label = (w.get("label") or "").strip().lower()
            if label == wanted or label.startswith(wanted + " ") or wanted in label.split():
                return w
    return None


class VisitEnder:
    """Ends a visit: End Visit, then the Continue participation dialog, then
    confirm it actually ended.

    The first of the visit-mode sub-autopilots to be split out. It owns one
    responsibility and reports one structured result, so the orchestrator above
    it never has to inspect page state itself.

    Two rules it will not break:

    - It ends a visit ONLY when the progress counter reads N/N. Ending is
      irreversible, so an unreadable or partial counter is a refusal, never a
      guess.
    - It will not act while an upload is still in flight, because a question
      whose file has not landed is not answered, whatever the counter says.

    Result: {"clicked", "confirmed", "reason", "progress_at_end", "steps"}.
    `confirmed` is the only field that means the visit is actually over.
    """

    def __init__(self, page, min_x=330):
        self.page = page
        self.min_x = min_x
        self.steps = []

    def _note(self, step):
        self.steps.append(step)

    def _result(self, clicked, confirmed, reason, progress=None):
        return {"clicked": clicked, "confirmed": confirmed, "reason": reason,
                "progress_at_end": progress, "steps": list(self.steps)}

    async def run(self):
        widgets = await extract_flutter_widgets(self.page)

        progress = _autopilot_progress(widgets)
        if not _visit_is_complete(progress):
            return self._result(
                False, False,
                f"progress reads {progress or 'unknown'} — not ending a visit that is "
                "not verified complete", progress)

        if _autopilot_upload_in_flight(_autopilot_panel(widgets, self.min_x)):
            return self._result(
                False, False,
                "an upload is still in flight — the form holding it is not actually "
                "answered yet", progress)

        # A modal left over from filling would swallow the click.
        if any(w.get("role") == "dialog" for w in widgets):
            how = await _autopilot_clear_dialog(self.page, widgets)
            self._note(f"cleared a dialog first ({how or 'could not'})")
            widgets = await extract_flutter_widgets(self.page)

        button = _end_visit_button(widgets)
        if not button:
            return self._result(False, False, "no End Visit button on screen", progress)

        await _autopilot_click(self.page, button, settle=1500, allow_reserved=True)
        self._note("clicked End Visit")

        confirmed = await self._confirm_participation()
        left = await self._left_visit_mode()

        if left:
            return self._result(True, True, "", progress)
        reason = ("clicked End Visit"
                  + (" and confirmed the dialog" if confirmed else
                     " but the Continue participation dialog was not answered")
                  + ", yet the Visit Mode screen is still showing")
        return self._result(True, False, reason, progress)

    async def _confirm_participation(self):
        """Answer the Continue participation dialog, if it appears.

        It may take a moment to render, and may chain, so this waits for it and
        then answers up to three dialogs. Returns whether anything was
        confirmed — False simply means none appeared, which is not a failure.
        """
        answered = False
        for _ in range(3):
            widgets = None
            for _wait in range(8):
                widgets = await extract_flutter_widgets(self.page)
                if any(w.get("role") == "dialog" for w in widgets):
                    break
                await self.page.wait_for_timeout(400)
            if not widgets or not any(w.get("role") == "dialog" for w in widgets):
                break
            button = _continue_button(widgets)
            if not button:
                how = await _autopilot_clear_dialog(self.page, widgets)
                self._note(f"unrecognised dialog after End Visit ({how or 'could not clear'})")
                break
            await _autopilot_click(self.page, button, settle=1200, allow_reserved=True)
            self._note(f"confirmed {(button.get('label') or '')[:40]!r}")
            answered = True
        return answered

    async def _left_visit_mode(self):
        """Leaving Visit Mode is how the end is confirmed — not the click."""
        for _ in range(12):
            widgets = await extract_flutter_widgets(self.page)
            if not _autopilot_visit_ok(widgets):
                return True
            await self.page.wait_for_timeout(500)
        return False


async def _autopilot_end_visit(page, min_x, report):
    """Thin adapter kept so callers and the report shape do not change."""
    result = await VisitEnder(page, min_x).run()
    report["end_visit"] = result
    return result["confirmed"]


def _visit_date_box(widgets, min_x):
    """The visit's own date field, but ONLY when it is genuinely empty.

    Returns None the moment it looks set. The field is visit-level and set
    once; rewriting a valid one is pointless and actively harmful, because
    typing into it re-opens the calendar picker whose modal then blocks the
    whole page. Emptiness is judged on the value AND on any date-looking text
    in the node, since these inputs do not reliably expose a value.
    """
    for w in widgets:
        if w.get("role") != "textbox":
            continue
        if (w.get("bounds") or {}).get("center_x", 10 ** 9) >= min_x:
            continue          # in the question panel, not the visit rail
        if (w.get("value") or "").strip():
            return None       # already answered
        if _DATE_TEXT_RE.search(w.get("label") or ""):
            return None       # shows a date even though value is unset
        return w
    return None


async def _autopilot_fill_visit_date(page, widgets, today, report, min_x=330):
    """Fill the visit's own "Actual visit date" field if it is still empty.

    It lives above the form, outside every question block, and is REQUIRED —
    so a form whose questions are all answered is still refused until this is
    set. Nothing in the question loop can reach it, which is why the submit
    was rejected six times with a message that named this field and no
    question. Returns True if it typed something.
    """
    # Located by POSITION, not by label: Novatek's date inputs expose an empty
    # accessible name, so matching on "actual visit date" found this field only
    # when the render happened to populate it.
    box = _visit_date_box(widgets, min_x)
    if not box:
        return False
    # Confirm against a fresh read before typing — a stale snapshot is how a
    # field that was already set got rewritten.
    box = _visit_date_box(await extract_flutter_widgets(page), min_x)
    if not box:
        return False
    await _autopilot_type(page, box, today)
    report["answered"].append({"question": "(visit) Actual visit date",
                               "action": f"typed '{today}'"})
    return True


def _block_has_error(blk):
    """True when the block shows an inline validation error — its field holds
    a WRONG value (not merely an empty one) and must be retyped."""
    return bool(_block_error_text(blk))


#: Flutter/Java DateFormat tokens -> strftime, longest first so "yyyy" is not
#: consumed as "yy" + "yy". Case matters: M is month, m is minute.
_PATTERN_TOKENS = [
    ("yyyy", "%Y"), ("yy", "%y"),
    ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"), ("M", "%-m"),
    ("dd", "%d"), ("d", "%-d"),
    ("HH", "%H"), ("H", "%-H"),
    ("hh", "%I"), ("h", "%-I"),
    ("mm", "%M"), ("m", "%-M"),
    ("ss", "%S"), ("s", "%-S"),
    ("a", "%p"),
]

#: Novatek states the format it wants inside the rejection itself — "Invalid
#: format (M/d/yyyy)", "Invalid time format (HH:mm)". That is far better than
#: any guess, so it is read straight out of the message.
_ERROR_FORMAT_RE = re.compile(r"\(\s*([yYMdDhHmsa/:.\- ]{3,})\s*\)")


#: Rejections that say what KIND of value the field takes. Same principle as
#: _format_from_error: the form knows, so ask it rather than guessing.
_ALPHA_ERROR_HINTS = (
    "only letters", "letters only", "alphabetic", "alphabets only",
    "no numbers", "cannot contain number", "must not contain number",
    "should not contain number", "only characters", "text only",
)
_NUMERIC_ERROR_HINTS = (
    "valid number", "numbers only", "only numbers", "numeric", "digits only",
    "must be a number", "enter a number",
)


def _kind_from_error(text):
    """"alpha", "numeric", or None — what the rejection says the field takes."""
    low = (text or "").lower()
    if any(h in low for h in _ALPHA_ERROR_HINTS):
        return "alpha"
    if any(h in low for h in _NUMERIC_ERROR_HINTS):
        return "numeric"
    return None


def _strftime_from_pattern(pattern):
    """A DateFormat pattern like "M/d/yyyy" as a strftime string."""
    out, i = [], 0
    while i < len(pattern):
        for token, repl in _PATTERN_TOKENS:
            if pattern.startswith(token, i):
                out.append(repl)
                i += len(token)
                break
        else:
            out.append(pattern[i])
            i += 1
    return "".join(out)


def _format_from_error(text):
    """The strftime format a validation message demands, or None.

    "Invalid format (M/d/yyyy)" -> "%-m/%-d/%Y". This turns a rejection into an
    instruction: rather than working down a ladder of guesses, the next answer
    is exactly the shape the form just said it wanted.
    """
    match = _ERROR_FORMAT_RE.search(text or "")
    if not match:
        return None
    pattern = match.group(1).strip()
    if not any(c in pattern for c in "yMdHhms"):
        return None
    return _strftime_from_pattern(pattern)


def _autopilot_variants(hint, today, now_hhmm, text_value, number_value):
    """Ordered candidate answers for one free-entry field.

    Index 0 is the normal default; every later index is what to try after the
    form rejected the previous one. This escalation is the whole point: the
    Flutter semantics tree does not say whether a box wants a number, a date or
    free text, so the first answer is a guess. Without alternatives a rejected
    guess is retyped identically forever, which is what made the autopilot stall
    on a validation error instead of working through it.
    """
    if "date" in hint:
        return [today, time.strftime("%m/%d/%Y"), time.strftime("%Y-%m-%d"),
                time.strftime("%d/%m/%Y")]
    if "time" in hint:
        return [now_hhmm, time.strftime("%I:%M %p"), time.strftime("%H:%M:%S")]
    if any(h in hint for h in _AUTOPILOT_TEXT_HINTS):
        # A name or free-text answer: never offer a bare number, which such a
        # field usually rejects outright.
        return [text_value, "Admin", "Nurse", "N/A"]
    if any(h in hint for h in _AUTOPILOT_NUMBER_HINTS):
        return [number_value, "1", "10", "0", "100"]
    return [text_value, number_value, "1", "N/A", "Test"]


#: Where the JARVIS server listens. The daemon runs in its own venv and cannot
#: import the provider layer, so LLM help is fetched over HTTP from the server
#: that started it.
JARVIS_SERVER_URL = os.environ.get("JARVIS_SERVER_URL", "http://127.0.0.1:4700")


def _ask_llm_blocking(payload, timeout):
    """POST one stuck question to the server's assist endpoint. Blocking; the
    caller runs it in an executor so the event loop keeps serving."""
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        JARVIS_SERVER_URL.rstrip("/") + "/assist/form-question",
        data=data, method="POST", headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _autopilot_ask_llm(blk, error_text, tried, timeout=90):
    """Ask the model what to do with ONE question the rules could not solve.

    Fallback, not driver: reached only after the deterministic ladder is spent,
    so an ordinary form still makes zero model calls. Any failure — server
    down, model unconfigured, timeout, malformed reply — returns None and the
    autopilot carries on exactly as it did before, which is why this can be
    added without making the fill less reliable.
    """
    options = sorted({(w.get("label") or "").strip()
                      for w in blk["members"]
                      if w.get("role") in ("button", "checkbox", "switch")
                      and (w.get("label") or "").strip()
                      and not _is_submit_label(w.get("label"))})
    fields = sorted({w.get("role") for w in blk["members"]
                     if w.get("role") in ("textbox", "button", "checkbox", "switch")})
    payload = {"question": blk["key"][:160], "error": error_text[:300],
               "fields": fields, "options": options[:12], "tried": tried[:8]}
    try:
        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, _ask_llm_blocking, payload, timeout)
    except Exception as e:
        print(f"[browser-daemon] llm assist unavailable: {e}", file=sys.stderr)
        return None
    if not isinstance(reply, dict) or reply.get("action") in (None, "skip"):
        return None
    return reply


#: The "Form Incomplete" card Novatek renders above the questions after a
#: refused submit. It names each unsatisfied question by title with a reason —
#: the app's own authoritative list of what is wrong, and the only signal for a
#: question that shows no inline message of its own (the time questions show
#: none at all, which is why a refusal used to arrive with nothing to act on).
_CARD_HEADER = "form incomplete"
_CARD_INTRO = "please correct the following"


def _autopilot_incomplete_items(panel):
    """Question titles named by the Form Incomplete card, in card order."""
    # Take the TOPMOST match, not the first in document order: the card is
    # wrapped in a group whose own centre sits mid-card, well below the items,
    # and anchoring to that collected nothing at all.
    headers = [w for w in panel
               if _CARD_HEADER in (w.get("label") or "").strip().lower()]
    if not headers:
        return []
    top = min(w["bounds"]["center_y"] for w in headers)
    # The card ends where the first numbered question begins.
    below = [w["bounds"]["center_y"] for w in panel
             if _AUTOPILOT_MARKER_RE.match((w.get("label") or "").strip())
             and w["bounds"]["center_y"] > top]
    bottom = min(below) if below else top + 800

    titles = []
    for w in sorted((x for x in panel if top < x["bounds"]["center_y"] < bottom),
                    key=lambda x: x["bounds"]["center_y"]):
        label = (w.get("label") or "").strip()
        low = label.lower()
        if not label or _CARD_HEADER in low or _CARD_INTRO in low:
            continue
        # Skip the reason line under each title; keep the title itself.
        if low.startswith("this question") or any(h in low for h in _AUTOPILOT_ERROR_HINTS):
            continue
        if label not in titles:
            titles.append(label)
    return titles


#: Text that appears once a submission has been accepted.
_SUBMIT_SUCCESS_HINTS = (
    "success", "submitted successfully", "saved successfully", "form submitted",
    "submission successful",
)
#: Text shown while the request is in flight.
_SUBMIT_BUSY_HINTS = ("loading", "submitting", "please wait", "saving", "processing")
#: Text for a backend-side rejection, as opposed to client-side validation.
_SUBMIT_FAILURE_HINTS = (
    "submission failed", "failed to submit", "could not be submitted",
    "something went wrong", "server error", "try again later",
)


def _autopilot_submit_state(widgets, panel):
    """("busy" | "success" | "failed" | "rejected" | None) from the page.

    None means the page says nothing yet — neither still working nor finished.
    """
    labels = " ".join((w.get("label") or "").lower() for w in widgets)
    if any(h in labels for h in _SUBMIT_BUSY_HINTS):
        return "busy"
    # An indeterminate progress bar is a spinner; the visit's progress ring
    # always carries a value, so it is not mistaken for one.
    if any(w.get("role") == "progressbar" and not str(w.get("value") or "").strip()
           for w in widgets):
        return "busy"
    if any(h in labels for h in _SUBMIT_FAILURE_HINTS):
        return "failed"
    if any(h in labels for h in _SUBMIT_SUCCESS_HINTS):
        return "success"
    if _autopilot_incomplete_items(panel) or any(
            _block_has_error(b) for b in _autopilot_blocks(panel)):
        return "rejected"
    return None


async def _autopilot_await_submit(page, min_x, before_progress, timeout_s=45):
    """Wait for the backend to answer a submit, then report what it said.

    Clicking Submit starts a loading indicator, and the outcome — a success
    banner, a validation card, or a backend failure — only exists once that
    clears. The previous code waited a fixed 1.5s and read immediately, so it
    routinely saw the state from BEFORE the request finished: a submission
    that was accepted still reported the old progress counter, and any action
    taken in that window raced the app.

    Returns (outcome, widgets) where outcome is one of "success", "failed",
    "rejected", "timeout" or "quiet" (finished, but the page said nothing
    either way — the caller falls back to comparing the progress counter).
    """
    deadline = time.time() + timeout_s
    widgets = await extract_flutter_widgets(page)
    settled = 0
    while time.time() < deadline:
        panel = _autopilot_panel(widgets, min_x)
        state = _autopilot_submit_state(widgets, panel)
        if state == "busy":
            settled = 0
        elif state in ("success", "failed", "rejected"):
            return state, widgets
        else:
            progress = _autopilot_progress(widgets)
            if progress and before_progress and progress != before_progress:
                return "success", widgets
            # Nothing said either way: only conclude once the page has stopped
            # changing, so a still-rendering result is not read as silence.
            settled += 1
            if settled >= 3:
                return "quiet", widgets
        await page.wait_for_timeout(400)
        widgets = await extract_flutter_widgets(page)
    return "timeout", widgets


async def _autopilot_clear_dialog(page, widgets):
    """Dismiss a blocking modal. Returns how it was cleared, or "" if it could
    not be, and "" also when there was nothing to clear.

    Accepting is the default — a date/time picker opens on today, which is the
    value wanted anyway. The "reason for answering X" modal has no OK and no
    Cancel and needs its textarea filled first. Escape is the last resort for
    a dialog nobody anticipated, such as the participant-queries panel.
    """
    if not any(w.get("role") == "dialog" for w in widgets):
        return ""
    how = await _autopilot_accept_dialog(page)
    if not how:
        field = next((w for w in widgets if w.get("role") == "textbox"
                      or "why you selected" in (w.get("label") or "").lower()), None)
        note = next((w for w in widgets if w.get("role") == "button"
                     and "add to visit note" in (w.get("label") or "").lower()), None)
        if field and note:
            await _autopilot_type(page, field, "Jarvis Reason")
            await _autopilot_click(page, note, settle=800)
            how = "reason note"
    if not how:
        close = next((w for w in widgets if w.get("role") == "button"
                      and (w.get("label") or "").strip().lower()
                      in ("cancel", "close", "dismiss", "back", "x",
                          "\u2715", "\u2716", "\u00d7")), None)
        if close:
            await _autopilot_click(page, close, settle=500)
            how = "close button"
    if not how:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        if not any(w.get("role") == "dialog"
                   for w in await extract_flutter_widgets(page)):
            how = "escape"
    return how


def _autopilot_is_stuck(reopened, before_state, after_state):
    """Whether the run is stuck, in the only sense that matters here.

    Stuck is not "slow" and not "a timer expired". It is: the form cannot be
    submitted, and the deterministic path cannot resolve what is blocking it —
    a correction round ran and changed nothing. That is the trigger for asking
    the model, and nothing else is.

    Args:
        reopened: how many questions the correction round re-opened.
        before_state / after_state: comparable snapshots of what is answered,
            taken either side of that round.
    """
    if reopened == 0:
        return True                      # refused, and nothing to point at
    return before_state == after_state   # corrections ran but changed nothing


def _autopilot_answer_state(panel, inner_h):
    """A comparable snapshot of what the form currently holds, used to tell a
    correction round that changed something from one that did not."""
    return tuple(sorted(
        (blk["key"], _block_is_answered(blk, inner_h), _block_error_text(blk)[:40])
        for blk in _autopilot_blocks(panel)))


async def _autopilot_reopen_from_card(page, tab_index, min_x, done, attempts, variant,
                                      report, protected=frozenset()):
    """Re-open exactly the questions the Form Incomplete card names.

    Matches each card title against the block titles and clears those blocks'
    state so the main loop answers them again. Returns how many were matched.
    """
    widgets = await extract_flutter_widgets(page)
    panel = _autopilot_panel(widgets, min_x)
    wanted = _autopilot_incomplete_items(panel)
    if not wanted:
        return 0

    report["incomplete_card"] = wanted
    blocks = {b["key"]: b for b in _autopilot_blocks(panel)}
    matched = 0
    for title in wanted:
        low = title.lower()
        for key, blk in blocks.items():
            if key in protected:
                continue
            if low in (blk.get("title") or "").lower() or low in key.lower():
                done.discard(key)
                attempts[key] = 0
                matched += 1
                report["corrections"].append({
                    "question": key[:80],
                    "error": f"Form Incomplete card: {title}",
                    "retry_with_variant": variant.get(key, 0),
                })
                break
    return matched


async def _autopilot_reopen_rejected(page, tab_index, min_x, done, attempts, variant,
                                     report, protected=frozenset()):
    """After a rejected submit, walk the whole form and re-open every question
    the form actually complained about.

    For each block showing an inline error: clear its "done" flag, reset its
    attempt budget (so the 3-attempt cap can never permanently abandon a
    question the form is explicitly blocking on), and advance it to the next
    candidate answer so the retry is different from what was just refused.

    Returns how many questions were re-opened. Zero means the submit was
    refused with no inline explanation, which the caller handles separately.
    """
    reopened = 0
    seen = set()
    for _ in range(40):
        widgets = await extract_flutter_widgets(page)
        panel = _autopilot_panel(widgets, min_x)
        for blk in _autopilot_blocks(panel):
            key = blk["key"]
            if key in seen or not _block_has_error(blk):
                continue
            if key in protected:
                continue          # an answered choice: never re-picked
            seen.add(key)
            done.discard(key)
            attempts[key] = 0
            variant[key] = variant.get(key, 0) + 1
            reopened += 1
            report["corrections"].append({
                "question": key[:80],
                "error": _block_error_text(blk)[:120],
                "retry_with_variant": variant[key],
            })
        at_bottom = any(_is_submit_label(w.get("label"))
                        for w in panel if w.get("role") == "button")
        if at_bottom:
            break
        await cmd_scroll({"direction": "down", "amount": 420, "tab_index": tab_index})
        await page.wait_for_timeout(250)
    return reopened


#: A filename in the block means the upload landed. Kept alongside the
#: checkmark test because the card shows the attached file's name too.
_ATTACHED_FILE_RE = re.compile(r"\.(pdf|png|jpe?g|docx?|csv|xlsx?)\b", re.IGNORECASE)


def _upload_state(blk):
    """"busy" | "done" | "none" for a file-upload question.

    The card shows a circular progress indicator on its right while the file is
    uploading, which becomes a checkmark on success. That distinction is the
    whole answer to "is this question answered": it is NOT answered until the
    upload completes, so the autopilot must neither move on nor press Submit
    while the indicator is spinning — a form submitted mid-upload looks filled
    and is not.

    "busy" is an indeterminate progressbar inside the block. The visit's own
    progress ring always carries a value and lives outside any block, so it is
    not mistaken for one.
    """
    for w in blk["members"]:
        if w.get("role") == "progressbar" and not str(w.get("value") or "").strip():
            return "busy"
    for w in blk["members"]:
        if _ATTACHED_FILE_RE.search(w.get("label") or ""):
            return "done"
    return "none"


def _block_is_upload(blk):
    """Whether this question takes a file at all."""
    return bool(_UPLOAD_KEYWORD_RE.search(blk.get("hint") or "")) or any(
        _UPLOAD_KEYWORD_RE.search(w.get("label") or "") for w in blk["members"])


def _autopilot_upload_in_flight(panel):
    """True while any upload on screen is still going.

    Nothing may be clicked — least of all Submit — until this clears.
    """
    return any(_upload_state(blk) == "busy" for blk in _autopilot_blocks(panel))


def _block_is_answered(blk, inner_h):
    """True when this question already holds a valid answer and shows no error.

    The question-level counterpart of skipping a submitted form. Re-answering
    a good answer is never free on this app: it re-opens date/time pickers,
    and changing a choice can collapse every dependent question (flipping
    "Was the dose administered per protocol?" from Yes to No hid questions
    2-7 and raised a mandatory "reason" modal). So a question is touched only
    when it is blank, or when the form itself flags it — an inline validation
    message, or a Form Incomplete card entry.
    """
    if _block_has_error(blk):
        return False
    if _block_is_upload(blk):
        # Answered only when the upload actually completed. A spinning
        # indicator means in flight; nothing means no file yet.
        return _upload_state(blk) == "done"
    members = [w for w in blk["members"]
               if w["bounds"]["center_y"] > blk["marker_y"] + 12
               and w["bounds"]["center_y"] < inner_h - 10]
    boxes = [w for w in members if w.get("role") == "textbox"]
    if any(not (w.get("value") or "").strip() for w in boxes):
        return False           # an empty field means unanswered, always
    if boxes:
        return True            # every visible field holds a value
    return any(w.get("value") == "checked" for w in members)


def _autopilot_visit_ok(widgets):
    """True only on the visit-mode screen — the autopilot's hard scope guard."""
    return any((w.get("label") or "").strip() == "Visit Mode" for w in widgets)


def _autopilot_profile_ok(widgets):
    """True on the participant profile screen (where visits are started)."""
    labels = [(w.get("label") or "") for w in widgets]
    return any("Visit Progress" in l or "Participant profile" in l for l in labels)


def _autopilot_progress(widgets):
    """The top-bar N/M forms-progress counter, e.g. '0/4'."""
    for w in widgets:
        if re.fullmatch(r"\d+\s*/\s*\d+", (w.get("label") or "").strip()):
            return w["label"].replace(" ", "")
    return None


def _autopilot_panel(widgets, min_x):
    """Widgets belonging to the question panel (right of the sidebar)."""
    out = []
    for w in widgets:
        b = w.get("bounds") or {}
        if (b.get("center_x") or 0) >= min_x and (b.get("center_y") or 0) > 100:
            out.append(w)
    return sorted(out, key=lambda w: (w["bounds"]["center_y"], w["bounds"]["center_x"]))


def _autopilot_blocks(panel):
    """Splits panel widgets into per-question blocks using the markers."""
    markers = [w for w in panel if _AUTOPILOT_MARKER_RE.match((w.get("label") or "").strip())]
    blocks = []
    for i, m in enumerate(markers):
        top = m["bounds"]["center_y"] - 25
        bottom = markers[i + 1]["bounds"]["center_y"] - 25 if i + 1 < len(markers) else 10 ** 9
        members = [w for w in panel if top <= w["bounds"]["center_y"] < bottom]
        title = ""
        for w in members:
            if w is m:
                continue
            if abs(w["bounds"]["center_y"] - m["bounds"]["center_y"]) <= 12 and w.get("label"):
                low = w["label"].strip().lower()
                if _CARD_HEADER in low or _CARD_INTRO in low:
                    continue     # the error card is not question 1's title
                title = w["label"]
                break
        extras = " ".join(
            w.get("label") or "" for w in members
            if w.get("role") == "widget" and w.get("label") and w["label"] != title
            and 12 < w["bounds"]["center_y"] - m["bounds"]["center_y"] <= 70)
        blocks.append({
            "key": f"{m['label']} {title}".strip(),
            "marker": (m.get("label") or "").strip(),
            "marker_y": m["bounds"]["center_y"],
            "title": title,
            "hint": _normalize_hint(f"{title} {extras}"),
            "members": members,
        })
    # "3." is a heading whose real inputs live under "3-1." and "3-2." — it has
    # no field of its own, so treating it as a question burned render-waits and
    # then abandoned it as "inputs never became actionable", which in turn kept
    # `pending` non-empty and stopped the run from ever reaching Submit.
    markers = {b["marker"] for b in blocks}
    for b in blocks:
        prefix = b["marker"].rstrip(".") + "-"
        b["is_container"] = any(other.startswith(prefix) for other in markers)
    return blocks


def _widget_sig(w):
    b = w.get("bounds") or {}
    return (w.get("role"), w.get("label"),
            round((b.get("center_x") or 0) / 25), round((b.get("center_y") or 0) / 25))


#: How much room a question needs below its marker before it counts as being
#: on screen. Novatek renders the input roughly 100px under its number, so a
#: marker sitting just above the fold has its field just below it: the loop saw
#: a "visible" question with nothing actionable in it, waited, then abandoned it
#: as "inputs never became actionable" instead of simply scrolling down to it.
_AUTOPILOT_BLOCK_MARGIN = 170

#: Labels that identify a file-upload control, matched on WHOLE WORDS. The
#: earlier substring test matched "file" inside "Profile" and so treated the
#: navbar's participant chip as an upload button.
_UPLOAD_KEYWORD_RE = re.compile(
    r"\b(upload|attach|browse|consent)\b|\bfiles?\b|\bchoose\s+file\b|"
    r"\bselect\s+file\b|\bsigned\s+consent\b",
    re.IGNORECASE,
)


#: Controls the autopilot must never click, whatever code path selects them.
#: These navigate away from or out of the form — the participant chip opens a
#: profile dialog that the autopilot cannot dismiss, which froze a visit walk
#: mid-run. "Add to visit note" is deliberately absent: the dialog guard needs
#: it to clear the mandatory reason modal.
_AUTOPILOT_NEVER_CLICK = (
    "view profile", "participant id", "prt-", "account admin", "notification",
    "end visit", "hold visit", "discard changes", "early termination",
    "view/edit visit note", "log out", "logout", "sign out", "navigation menu",
)

#: The top navigation strip. Nothing the autopilot legitimately clicks lives
#: above this, and everything up there navigates away from the form.
_AUTOPILOT_TOP_BAR_Y = 90


def _autopilot_click_forbidden(w):
    """Why this widget must not be clicked, or "" when it is safe."""
    label = (w.get("label") or "").strip().lower()
    for reserved in _AUTOPILOT_NEVER_CLICK:
        if reserved in label:
            return f"reserved control {reserved!r}"
    if (w.get("bounds") or {}).get("center_y", 999) < _AUTOPILOT_TOP_BAR_Y:
        return "in the top navigation bar"
    return ""


async def _autopilot_click(page, w, settle=180, allow_reserved=False):
    """Click a widget, unless it is something that would leave the form.

    `allow_reserved` is the deliberate override, used only by
    _autopilot_end_visit after the progress counter has verified every form is
    submitted. The guard exists to stop STRAY clicks on those controls, not to
    forbid the one place a reserved control is genuinely the right action.

    The single choke point for every click the autopilot makes, so the guard
    holds no matter which code path chose the widget — the participant-profile
    click that froze a visit walk came from the dropdown-option picker, not
    from the question loop.
    """
    forbidden = "" if allow_reserved else _autopilot_click_forbidden(w)
    if forbidden:
        print(f"[browser-daemon] refused to click {(w.get('label') or '')[:40]!r}: {forbidden}",
              file=sys.stderr)
        return False
    b = w["bounds"]
    # Every click the autopilot makes, logged. A stray click that navigates out
    # of the visit is otherwise impossible to attribute after the fact — the
    # page has already changed by the time anything notices.
    print(f"[browser-daemon] click {(w.get('label') or '')[:46]!r} "
          f"role={w.get('role')} at ({int(b['center_x'])},{int(b['center_y'])})",
          file=sys.stderr)
    await page.mouse.click(b["center_x"], b["center_y"])
    await page.wait_for_timeout(settle)
    return True


async def _autopilot_type(page, w, text):
    await _autopilot_click(page, w, settle=120)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(str(text), delay=8)
    await page.wait_for_timeout(80)


async def _autopilot_accept_dialog(page, extra_labels=()):
    """Accepts an open picker/confirmation dialog by clicking its OK-style
    button (calendar and time pickers default to now/today, so accepting is
    exactly the 'current date / current time' rule). Returns the label it
    clicked, or None when no dialog button was found."""
    widgets = await extract_flutter_widgets(page)
    accept = tuple(extra_labels) + _AUTOPILOT_DIALOG_ACCEPT
    for want in accept:
        for x in widgets:
            if x.get("role") == "button" and (x.get("label") or "").strip().lower() == want:
                await _autopilot_click(page, x, settle=450)
                return want
    return None


async def _autopilot_first_dropdown_option(page, before_widgets):
    """After a dropdown/unit button was clicked, picks the FIRST option the
    click revealed (widgets present now but not before)."""
    await page.wait_for_timeout(300)
    before = {_widget_sig(w) for w in before_widgets}
    widgets = await extract_flutter_widgets(page)
    fresh = [w for w in widgets if _widget_sig(w) not in before and w.get("label")]
    fresh = [w for w in fresh if w.get("role") in ("option", "button", "menuitem", "widget")]
    # A re-render makes unrelated widgets look "fresh". Anything outside the
    # question area is not a dropdown option — this is how the participant
    # chip in the top bar got clicked, opening a profile dialog the autopilot
    # could not dismiss.
    fresh = [w for w in fresh
             if (w.get("bounds") or {}).get("center_y", 0) > _AUTOPILOT_TOP_BAR_Y
             and not _autopilot_click_forbidden(w)]
    fresh.sort(key=lambda w: (0 if w.get("role") == "option" else 1,
                              w["bounds"]["center_y"], w["bounds"]["center_x"]))
    if fresh:
        await _autopilot_click(page, fresh[0], settle=250)
        return fresh[0].get("label")
    await page.keyboard.press("Escape")
    return None


async def _autopilot_sign(page, w, username, password, report):
    """Clicks a Sign button and completes the credentials dialog."""
    if not username or not password:
        # Signing with blank fields would confirm an empty dialog and look like
        # a success — leave the question unresolved so the caller reports it.
        report["unresolved"].append(
            "signature question: no Novatek login configured (set novatek.username / "
            "novatek.password in config.json, or NOVATEK_USERNAME / NOVATEK_PASSWORD)")
        return
    await _autopilot_click(page, w, settle=700)
    widgets = await extract_flutter_widgets(page)
    dialog_present = any(x.get("role") == "dialog" for x in widgets)
    boxes = [x for x in widgets if x.get("role") == "textbox"
             and (x.get("label") or "").strip().lower() != "search..."]
    boxes.sort(key=lambda x: x["bounds"]["center_y"])
    if not dialog_present or not boxes:
        report["unresolved"].append("signature dialog did not appear as expected")
        return
    await _autopilot_type(page, boxes[0], username)
    if len(boxes) > 1:
        await _autopilot_type(page, boxes[1], password)
    widgets = await extract_flutter_widgets(page)
    for x in widgets:
        low = (x.get("label") or "").strip().lower()
        if x.get("role") == "button" and (
                _is_sign_label(low) or low in ("confirm", "ok", "submit", "verify", "login")):
            await _autopilot_click(page, x, settle=700)
            return
    report["unresolved"].append("signature dialog: no confirm button found")


async def _autopilot_scroll_to_form_top(page, tab_index, min_x):
    """Scrolls the question panel back to question 1 for the pre-submit sweep."""
    for _ in range(20):
        widgets = await extract_flutter_widgets(page)
        blocks = _autopilot_blocks(_autopilot_panel(widgets, min_x))
        if any(b["key"].startswith("1.") and b["marker_y"] > 110 for b in blocks):
            return widgets
        await cmd_scroll({"direction": "up", "amount": 900, "tab_index": tab_index})
    return await extract_flutter_widgets(page)


async def cmd_autofill_form(payload):
    """Deterministically fills and submits the form currently open on the
    Novatek VISIT MODE screen. Answers every question top-to-bottom with the
    standard defaults, VERIFIES each answer actually registered (a question
    that is visible but unanswered is retried immediately, never left for a
    lucky later round), runs a full top-to-bottom sweep before Submit, and
    reports the before/after N/M progress counter plus anything unresolved."""
    text_value = payload.get("text_value") or "test"
    number_value = str(payload.get("number_value") or "55")
    file_path = payload.get("file_path") or "/home/proslayer/AndroidStudioProjects/jarvis_full/Informed_Consent Template.pdf"
    # No credential lives in this file. The JARVIS connector resolves the login
    # from config/env and sends it in the payload; the env vars are the fallback
    # for driving this daemon directly. Empty means the signature step will
    # report an unresolved question rather than signing as a guessed user.
    username = payload.get("username") or os.environ.get("NOVATEK_USERNAME") or ""
    password = payload.get("password") or os.environ.get("NOVATEK_PASSWORD") or ""
    min_x = int(payload.get("panel_min_x", 330))
    # Raised from 100: each rejected submit now costs a full top-to-bottom
    # correction sweep, and the point is to keep going until the form is
    # accepted rather than to stop at a fixed budget.
    max_rounds = int(payload.get("max_rounds", 250))
    # How many times a rejected submit is corrected and retried before giving
    # up. Every retry re-answers only the questions the form objected to, with
    # a different value than the one it refused.
    max_submit_attempts = int(payload.get("max_submit_attempts", 6))
    # Set false for a strictly zero-AI run (offline, or to time the rules alone).
    llm_assist = payload.get("llm_assist", True)
    tab_index = payload.get("tab_index")

    page = await get_active_page(tab_index=tab_index)
    if not await is_flutter_page(page):
        return {"error": "autofill only works on the Novatek Flutter app"}

    today = time.strftime("%-m/%-d/%Y")
    now_hhmm = time.strftime("%H:%M")

    report = {"answered": [], "unresolved": [], "corrections": [], "llm_assists": [],
              "incomplete_card": [], "submit_outcome": None, "rounds": 0,
              "submitted": False, "submit_verified": False, "submit_attempts": 0,
              "progress_before": None, "progress_after": None}
    done = set()          # blocks whose answers were VERIFIED in place
    attempts = {}         # per-block answer attempts (cap 3, then unresolved)
    variant = {}          # per-block index into _autopilot_variants — bumped
                          # every time the form rejects that block's answer
    units_done = set()    # blocks whose unit dropdown was already picked
    swept = False         # pre-submit top-to-bottom sweep completed
    blanket_retry = False # a no-inline-error refusal has already been retried
    llm_asked = set()     # questions already escalated (at most one ask each)
    choice_answered = set()  # answered by selecting an option — NEVER re-picked
    tried = {}            # per-question values the form has already refused
    resubmits = 0
    render_waits = 0      # rounds spent waiting for a visible block's inputs to render

    widgets = await extract_flutter_widgets(page)

    # Clear a blocking modal BEFORE deciding whether we are on Visit Mode. A
    # dialog covers the screen, so this check saw eight widgets, none of them
    # "Visit Mode", and refused to start — while the guard that would have
    # dismissed it lives inside the round loop it never reached. Arriving with
    # one open is normal: a file upload legitimately ends on a date picker.
    for _ in range(3):
        if not any(w.get("role") == "dialog" for w in widgets):
            break
        how = await _autopilot_clear_dialog(page, widgets)
        report["answered"].append({"question": "(dialog on entry)",
                                   "action": f"dismissed via {how or 'no button found'}"})
        widgets = await extract_flutter_widgets(page)
        if not how:
            break

    if not _autopilot_visit_ok(widgets):
        return {"error": "Not on the Visit Mode screen — the autopilot is restricted to visit mode. "
                         "Start the visit first."}
    report["progress_before"] = _autopilot_progress(widgets)

    # Required, lives outside every question block, and blocks submission on
    # its own. Do it up front rather than discovering it via a refusal.
    if not payload.get("skip_visit_date") and await _autopilot_fill_visit_date(
            page, widgets, today, report, min_x):
        widgets = await extract_flutter_widgets(page)

    stagnant = 0
    stray_dialogs = 0
    for round_no in range(1, max_rounds + 1):
        report["rounds"] = round_no

        # A modal blocks every field under it, and one stray picker wedges the
        # whole run: a date picker left open by a mis-aimed click made the rest
        # of a visit walk meaningless, including the sidebar checkmark scan,
        # which then read a dimmed page. Clear any dialog before doing anything
        # else. Accepting is the right default — a date/time picker opens on
        # today, which is the value the autopilot wants anyway — and Cancel is
        # the fallback when nothing accepts.
        if any(w.get("role") == "dialog" for w in widgets) and stray_dialogs < 8:
            stray_dialogs += 1
            accepted = await _autopilot_clear_dialog(page, widgets)
            report["answered"].append({"question": "(stray dialog)",
                                       "action": f"dismissed via {accepted or 'no button found'}"})
            widgets = await extract_flutter_widgets(page)
            continue
        inner_h = await page.evaluate("() => window.innerHeight")
        panel = _autopilot_panel(widgets, min_x)
        blocks = _autopilot_blocks(panel)
        acted = False

        for blk in blocks:
            key = blk["key"]
            # A "done" block is still re-checked every time it is on screen:
            # if its textbox shows no value, the answer didn't register and
            # it is retried NOW — this is the fix for visible-but-skipped
            # questions that previously waited for a lucky later round.
            if blk["marker_y"] >= inner_h - _AUTOPILOT_BLOCK_MARGIN:
                continue
            if blk.get("is_container"):
                # Answered by its sub-questions; it has no input of its own.
                done.add(key)
                continue
            hint = blk["hint"]
            below_title = [w for w in blk["members"]
                           if w["bounds"]["center_y"] > blk["marker_y"] + 12
                           and w["bounds"]["center_y"] < inner_h - 10]
            boxes_empty = [w for w in below_title if w.get("role") == "textbox" and not w.get("value")]
            error_text = _block_error_text(blk)
            error_here = bool(error_text)
            if error_here:
                # A rejected value (e.g. a date that landed in a number field)
                # is worse than an empty one — retype every field in the block.
                boxes_empty = [w for w in below_title if w.get("role") == "textbox"]
            if key in done and not boxes_empty and not error_here:
                continue
            # NEVER re-touch a question that already holds a valid answer —
            # a filled, error-free field (pre-existing or from a previous
            # round), a checked option, or an already-attached file counts as
            # answered on sight, with zero interaction.
            if not error_here:
                has_filled_box = any(w.get("role") == "textbox" and w.get("value")
                                     for w in below_title)
                any_empty_box = bool(boxes_empty)
                already_checked = any(w.get("value") == "checked" for w in below_title)
                already_uploaded = ("upload" in hint or "attach" in hint or "file" in hint) and any(
                    ".pdf" in (w.get("label") or "").lower() for w in blk["members"])
                # An EMPTY textbox always wins: the question is not answered,
                # whatever else in the block looks set.
                #
                # Novatek's time questions carry a "Use 24-hour format" switch
                # that ships already checked. Counting that as the answer marked
                # "3-2. Dose Start Time" and "4-2. Dose End Time" done with their
                # Hours:Minutes fields still blank — the form then refused the
                # submit with "This question must be answered" and nothing
                # inline to point at, because from the autopilot's side both
                # questions looked complete.
                if not any_empty_box and (has_filled_box or already_checked or already_uploaded):
                    done.add(key)
                    continue
            if attempts.get(key, 0) >= 3:
                # The deterministic ladder is spent on this question. Ask the
                # model once — it can read a validation phrasing the substring
                # rules never anticipated — then go back to running on rails.
                if llm_assist and key not in llm_asked:
                    llm_asked.add(key)
                    advice = await _autopilot_ask_llm(blk, error_text, tried.get(key, []))
                    if advice:
                        applied = None
                        if advice["action"] == "type":
                            box = next((w for w in below_title
                                        if w.get("role") == "textbox"), None)
                            if box:
                                await _autopilot_type(page, box, advice["value"])
                                tried.setdefault(key, []).append(advice["value"])
                                applied = f"llm: typed {advice['value']!r}"
                        elif advice["action"] == "click":
                            target = next((w for w in below_title
                                           if (w.get("label") or "").strip().lower()
                                           == advice["label"].strip().lower()), None)
                            if target:
                                await _autopilot_click(page, target, settle=250)
                                applied = f"llm: clicked {advice['label']!r}"
                        if applied:
                            attempts[key] = 0     # earned a fresh deterministic budget
                            done.discard(key)
                            report["llm_assists"].append({
                                "question": key[:80], "error": error_text[:100],
                                "did": applied, "why": advice.get("reason", "")})
                            report["answered"].append({"question": key[:80], "action": applied})
                            acted = True
                            continue
                continue
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] == 3:
                report["unresolved"].append(f"{key}: still unanswered after 3 attempts")
            actions_here = []

            sign_btn = next((w for w in below_title if w.get("role") == "button"
                             and _is_sign_label(w.get("label"))), None)
            upload_hit = ("upload" in hint or "attach" in hint) or any(
                any(k in (w.get("label") or "").lower() for k in ("upload", "attach", "browse", "choose file"))
                for w in below_title if w.get("role") == "button")
            checkboxes = [w for w in below_title if w.get("role") in ("checkbox", "switch")
                          and w.get("value") != "checked"]
            has_box = any(w.get("role") == "textbox" for w in below_title)
            # Buttons count as answer options ONLY in a block with no textbox —
            # next to a textbox a button is a unit dropdown or calendar icon.
            options = [] if has_box else [
                w for w in below_title if w.get("role") == "button" and w.get("label")
                and not _is_sign_label(w.get("label"))
                and not _is_submit_label(w.get("label"))]
            # A button beside a DATE or TIME box is a calendar/clock picker, not
            # a unit dropdown. Clicking it opened a picker that wiped the value
            # just typed — which is why a correctly formatted date still came
            # back "Invalid format".
            is_datetime = ("date" in hint or "time" in hint)
            unit_btns = [] if is_datetime else [
                w for w in below_title if has_box and w.get("role") == "button"
                and len((w.get("label") or "").strip()) <= 14
                and not _is_sign_label(w.get("label"))
                and not _is_submit_label(w.get("label"))]

            if sign_btn:
                await _autopilot_sign(page, sign_btn, username, password, report)
                actions_here.append("signature")
            elif upload_hit:
                res = await cmd_upload_file({"file_path": file_path, "tab_index": tab_index})
                # Both are successes. "file_uploaded" is the DOM path
                # (set_input_files on a real <input type=file>); "file_selected"
                # is the Flutter path, where the daemon intercepts the native
                # file chooser and picks the file for it. Treating the latter as
                # a failure made the autopilot retry a completed upload, which
                # re-opened the chooser — the modal that kept stalling the walk.
                if res.get("status") in ("file_uploaded", "file_selected"):
                    actions_here.append("upload")
                    # Consent uploads chain calendar + time pickers; both
                    # default to now, so accepting them IS the current
                    # date/time rule. Two dialogs at most.
                    for _ in range(2):
                        if not await _autopilot_accept_dialog(page):
                            break
                        actions_here.append("accepted picker dialog")
                else:
                    report["unresolved"].append(f"{key}: upload failed ({res.get('error') or res.get('status')})")
            elif boxes_empty:
                # On a retry round, a swallowed 'test' means a digit-only
                # field — go straight to the number default.
                candidates = _autopilot_variants(hint, today, now_hhmm,
                                                 text_value, number_value)
                in_range = _autopilot_range_value(blk)
                if in_range and not any(k in hint for k in ("date", "time")):
                    # The question states its own valid bands — start there
                    # rather than with a default that may be outside them.
                    candidates = [in_range] + [c for c in candidates if c != in_range]
                vi = variant.get(key, 0)
                if error_here and vi == 0:
                    # First rejection of an untried block: skip past the plain
                    # text default rather than retyping what was just refused.
                    vi = variant[key] = 1
                elif attempts[key] > 1 and vi == 0:
                    # Repeatedly typed and still empty — the field is swallowing
                    # the value, which in practice means it is digit-only.
                    vi = variant[key] = 1
                # The form names the format it wants in its own rejection —
                # obey that in preference to guessing.
                demanded = _format_from_error(error_text) if error_here else None
                kind = _kind_from_error(error_text) if error_here else None
                if demanded:
                    value = time.strftime(demanded)
                    actions_here.append(f"format from validator: {demanded}")
                elif kind == "alpha":
                    # The field said letters only. Every numeric candidate is
                    # guaranteed to be refused, so leave the ladder entirely.
                    alpha = [text_value, "Admin", "Nurse", "Staff"]
                    value = alpha[vi % len(alpha)]
                    actions_here.append("validator says letters only")
                elif kind == "numeric":
                    numeric = [number_value, "1", "10", "0"]
                    value = numeric[vi % len(numeric)]
                    actions_here.append("validator says numbers only")
                else:
                    # Cycle, never clamp: clamping parks on the LAST candidate
                    # forever, and if that one is the invalid one the retry can
                    # never succeed no matter how many times it runs.
                    value = candidates[vi % len(candidates)]
                tried.setdefault(key, []).append(value)
                for box in boxes_empty:
                    await _autopilot_type(page, box, value)
                    actions_here.append(f"typed '{value}'" + (f" (variant {vi})" if vi else ""))
                if unit_btns and key not in units_done:
                    units_done.add(key)
                    snapshot = await extract_flutter_widgets(page)
                    await _autopilot_click(page, unit_btns[0], settle=250)
                    picked = await _autopilot_first_dropdown_option(page, snapshot)
                    actions_here.append(f"unit '{picked}'" if picked else "unit dropdown: nothing to pick")
            elif checkboxes:
                await _autopilot_click(page, checkboxes[0])
                choice_answered.add(key)
                actions_here.append("checked first box")
            elif options:
                if any(w.get("value") == "checked" for w in below_title):
                    actions_here.append("already answered")
                else:
                    # Normally the first option; a later one once the form has
                    # rejected this block, since conditional forms do refuse
                    # particular choices.
                    pick = options[min(variant.get(key, 0), len(options) - 1)]
                    await _autopilot_click(page, pick)
                    choice_answered.add(key)
                    actions_here.append(f"clicked '{(pick.get('label') or '')[:40]}'")
            else:
                attempts[key] -= 1  # nothing actionable visible yet — not a real attempt
                continue

            if actions_here:
                report["answered"].append({"question": key[:80], "action": "; ".join(actions_here)})
                acted = True

        # Verification pass: only blocks whose visible inputs now hold a value
        # are marked done; the rest stay eligible for an immediate retry.
        widgets = await extract_flutter_widgets(page)
        for blk in _autopilot_blocks(_autopilot_panel(widgets, min_x)):
            if blk["marker_y"] >= inner_h - _AUTOPILOT_BLOCK_MARGIN or attempts.get(blk["key"], 0) == 0:
                continue
            empty = [w for w in blk["members"] if w.get("role") == "textbox" and not w.get("value")
                     and w["bounds"]["center_y"] > blk["marker_y"] + 12
                     and w["bounds"]["center_y"] < inner_h - 10]
            if _block_has_error(blk):
                # The form refused this value. Retyping the same thing would be
                # refused identically, so step to the next candidate answer.
                done.discard(blk["key"])
                variant[blk["key"]] = variant.get(blk["key"], 0) + 1
            elif empty:
                done.discard(blk["key"])
            else:
                done.add(blk["key"])

        panel = _autopilot_panel(widgets, min_x)
        submit = next((w for w in panel if w.get("role") == "button"
                       and _is_submit_label(w.get("label"))), None)
        blocks_now = _autopilot_blocks(panel)
        all_keys = {b["key"] for b in blocks_now}
        pending = all_keys - done - {k for k, n in attempts.items() if n >= 3}
        visible_pending = [b for b in blocks_now
                           if b["key"] in pending and b["marker_y"] < inner_h - _AUTOPILOT_BLOCK_MARGIN]

        if _autopilot_upload_in_flight(panel):
            # A file is still uploading. That question is not answered yet, so
            # pressing Submit now would submit a form that only looks filled.
            # Do nothing at all until the indicator clears.
            await page.wait_for_timeout(600)
            widgets = await extract_flutter_widgets(page)
            continue

        if submit and not pending:
            # `blocks_now` empty means this is a TABLE form — Concomitant
            # Medications and friends carry no numbered questions at all, just
            # an entries table with "Add New Entry" and "Submit Form". There is
            # nothing to fill and nothing to sweep for, so submit straight away
            # rather than scrolling 20 times looking for a question 1 that does
            # not exist.
            if not swept and blocks_now:
                # Full sweep before Submit: back to question 1, then walk down
                # re-verifying every screenful, so nothing scrolled past
                # unanswered can slip through.
                swept = True
                widgets = await _autopilot_scroll_to_form_top(page, tab_index, min_x)
                continue
            while submit and submit["bounds"]["center_y"] >= inner_h - 20:
                await cmd_scroll({"direction": "down", "amount": 400, "tab_index": tab_index})
                widgets = await extract_flutter_widgets(page)
                submit = next((w for w in _autopilot_panel(widgets, min_x)
                               if w.get("role") == "button"
                               and _is_submit_label(w.get("label"))), None)
            if not submit:
                continue
            await _autopilot_click(page, submit, settle=300)
            resubmits += 1
            report["submit_attempts"] = resubmits
            report["submitted"] = True
            # Do nothing until the request has actually finished.
            outcome, widgets = await _autopilot_await_submit(
                page, min_x, report["progress_before"])
            report["submit_outcome"] = outcome
            report["progress_after"] = _autopilot_progress(widgets)
            advanced = (report["progress_after"] or "") != (report["progress_before"] or "")
            still_open = any(_is_submit_label(w.get("label"))
                             for w in _autopilot_panel(widgets, min_x))
            if outcome in ("failed", "rejected"):
                report["submit_verified"] = False
            elif outcome == "success":
                report["submit_verified"] = True
            else:
                # "quiet" or "timeout": fall back to the observable signals.
                report["submit_verified"] = advanced or not still_open
            if outcome == "failed":
                report["unresolved"].append(
                    "the backend rejected the submission — retrying will not help "
                    "until whatever it objected to is fixed")
            if report["submit_verified"]:
                break

            # ---- the form refused the submission -------------------------
            # Do not stop here: find what it objected to, answer exactly that
            # differently, and submit again. A form is only done when it has
            # been ACCEPTED, and the validator is the one source of truth
            # about what is still wrong with it.
            report["submitted"] = False
            if resubmits >= max_submit_attempts:
                report["unresolved"].append(
                    f"submit refused {resubmits} times — stopping. Last errors: "
                    + "; ".join(str(c.get("error", "")) for c in report["corrections"][-3:]))
                break

            widgets = await _autopilot_scroll_to_form_top(page, tab_index, min_x)
            # Only revisit the visit date when the refusal actually mentions it;
            # otherwise leave a field that is already set completely alone.
            refusal_text = " ".join((w.get("label") or "").lower() for w in widgets)
            if "actual visit date" in refusal_text:
                if await _autopilot_fill_visit_date(page, widgets, today, report, min_x):
                    widgets = await extract_flutter_widgets(page)
            # The card names what is missing outright; the inline scan is the
            # fallback for a refusal it does not cover.
            reopened = await _autopilot_reopen_from_card(
                page, tab_index, min_x, done, attempts, variant, report,
                protected=choice_answered)
            reopened += await _autopilot_reopen_rejected(
                page, tab_index, min_x, done, attempts, variant, report,
                protected=choice_answered)
            if not reopened:
                # Refused with no inline error anywhere. Escalate GENTLY: the
                # first pass only returns abandoned questions to the queue, and
                # leaves every verified answer alone. Bumping every variant here
                # used to flip a correct "Yes" to "No", which on a conditional
                # form changes which questions exist at all — turning one
                # refusal into a worse form than we started with.
                if not blanket_retry:
                    blanket_retry = True
                    attempts.clear()
                    report["corrections"].append({
                        "question": "(whole form)",
                        "error": "submit refused with no inline validation message",
                        "retry_with_variant": "re-queued abandoned questions only",
                    })
                else:
                    # Still refused, and still nothing to point at. Re-queue
                    # ONLY questions that are genuinely blank — never rewrite
                    # one that already holds a valid answer. The card and the
                    # inline messages are the sole authority on what is wrong;
                    # guessing beyond them is what turned a correct "Yes" into
                    # "No" and collapsed the form.
                    requeued = 0
                    for blk in _autopilot_blocks(_autopilot_panel(widgets, min_x)):
                        k = blk["key"]
                        if k in choice_answered or _block_is_answered(blk, inner_h):
                            continue
                        done.discard(k)
                        attempts[k] = 0
                        requeued += 1
                    report["corrections"].append({
                        "question": "(whole form)",
                        "error": "refused with no inline message and no card entry",
                        "retry_with_variant": f"re-queued {requeued} blank question(s) only",
                    })
                    if not requeued:
                        report["unresolved"].append(
                            "submit refused but every question already holds a valid answer — "
                            "stopping rather than rewriting good answers")
                        break
            swept = False
            widgets = await _autopilot_scroll_to_form_top(page, tab_index, min_x)
            continue

        # SCROLLING IS EARNED, NEVER TIMED: while any visible question is
        # still unanswered, the loop stays on this screenful and retries —
        # it never scrolls away from pending work (the "skips questions and
        # scrolls too fast" failure on long forms).
        if visible_pending:
            if acted:
                continue
            # A question is visible but its inputs aren't in the semantics
            # tree yet (Flutter renders lazily) — wait for the render
            # instead of scrolling past it.
            render_waits += 1
            if render_waits <= 4:
                await page.wait_for_timeout(400)
                widgets = await extract_flutter_widgets(page)
                continue
            for b in visible_pending:
                attempts[b["key"]] = 3
                report["unresolved"].append(f"{b['key']}: inputs never became actionable")
        render_waits = 0

        # Scroll at most ~45% of the viewport so revealed content always
        # overlaps the previous screenful, then WAIT until the scroll
        # actually surfaces new questions (or Submit) before moving on.
        await cmd_scroll({"direction": "down",
                          "amount": max(300, min(500, int(inner_h * 0.45))),
                          "tab_index": tab_index})
        widgets = await extract_flutter_widgets(page)
        for _ in range(6):
            fresh_panel = _autopilot_panel(widgets, min_x)
            fresh_keys = {b["key"] for b in _autopilot_blocks(fresh_panel)}
            submit_visible = any(_is_submit_label(w.get("label"))
                                 for w in fresh_panel if w.get("role") == "button")
            if (fresh_keys - all_keys) or submit_visible:
                break
            await page.wait_for_timeout(350)
            widgets = await extract_flutter_widgets(page)
        fresh_keys = {b["key"] for b in _autopilot_blocks(_autopilot_panel(widgets, min_x))}
        if not (fresh_keys - all_keys):
            stagnant += 1
            if stagnant >= 3:
                report["unresolved"].append("reached the bottom without finding Submit — stopped")
                break
        else:
            stagnant = 0

    # "done" means the form was ACCEPTED, not merely that Submit was clicked —
    # a click the validator rejected leaves the form unfinished, and the caller
    # (cmd_autofill_visit, or the model) must not move on from it.
    report["status"] = "autofill_done" if report["submit_verified"] else "autofill_incomplete"
    report["widgets"] = _slim_widgets(widgets)
    return report


def _png_pixels(data):
    """Minimal stdlib PNG decoder (8-bit RGB/RGBA) — returns (w, h, channels,
    flat bytearray). Used to read the sidebar checkmarks, which exist only as
    drawn pixels: the semantics tree shows identical buttons either way."""
    import struct
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, w, h, colort, idat = 8, 0, 0, 6, b""
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        pos += 8
        chunk = data[pos:pos + ln]
        pos += ln + 4
        if typ == b"IHDR":
            w, h, _bitd, colort = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[colort]
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, out


async def _sidebar_checkmarks(page, buttons):
    """Which sidebar form buttons carry the green completion checkmark.

    Self-calibrating on purpose. Every earlier version depended on knowing the
    screenshot-to-CSS ratio up front, and every source for it was wrong here:
    window.innerHeight gave a factor that made five of seven completed forms
    read as open, and a clip-derived factor fared no better. The semantics
    bounds are equally unhelpful about WHERE the tick is drawn — rows report a
    right edge near 304 while the tick renders around 271-289 — so any sampling
    window aimed from them is a guess.

    So nothing is assumed. Find every green blob in the sidebar column, then
    solve for the ratio that best lines those blobs up with the row centres,
    and use it. A wrong answer here is expensive: a completed form read as open
    is re-opened and re-answered, which is the exact thing this prevents.
    """
    if not buttons:
        return []
    try:
        shot = await page.screenshot()
        width, height, channels, pixels = _png_pixels(shot)
    except Exception as e:
        print(f"[browser-daemon] checkmark pixel scan failed: {e}", file=sys.stderr)
        return [False] * len(buttons)

    # The sidebar is the left column; a third of the width covers it at any
    # plausible ratio, and nothing else there is green.
    green_per_row = {}
    for yy in range(0, height, 2):
        base = yy * width * channels
        count = 0
        for xx in range(0, max(2, width // 3), 2):
            i = base + xx * channels
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            if g > 110 and g > r + 35 and g > b + 35:
                count += 1
        if count:
            green_per_row[yy] = count

    clusters, current = [], []
    for y in sorted(green_per_row):
        if current and y - current[-1] > 12:
            clusters.append(current)
            current = []
        current.append(y)
    if current:
        clusters.append(current)
    centres = [sum(c) // len(c) for c in clusters
               if sum(green_per_row[y] for y in c) >= 8]
    if not centres:
        return [False] * len(buttons)

    rows = [b["bounds"]["center_y"] for b in buttons]
    # Solve for the ratio: every (blob, row) pair proposes one, and the right
    # one explains the most blobs at once.
    best_scale, best_hits = 1.0, -1
    for centre in centres:
        for row_y in rows:
            if row_y <= 0:
                continue
            candidate = centre / row_y
            if not 0.2 <= candidate <= 4.0:
                continue
            hits = sum(1 for c in centres
                       if any(abs(c - r * candidate) < 16 for r in rows))
            if hits > best_hits:
                best_scale, best_hits = candidate, hits

    return [any(abs(centre - b["bounds"]["center_y"] * best_scale) < 16
                for centre in centres)
            for b in buttons]


async def cmd_autofill_visit(payload):
    """Fills and submits EVERY form of the visit-mode screen. Rides Novatek's
    own flow: each verified submission auto-selects the next unsubmitted form,
    so the loop just fills whatever is open; the sidebar is only consulted
    (via the pixel checkmark scan) when nothing is auto-selected. Stops when
    the N/M progress counter is full or a form fails to advance it.

    Then ends the visit — that is the flow: fill, submit, verify, end. The
    Actual visit date is filled first, since a visit cannot be ended without
    it, and End Visit is clicked ONLY once the counter reads N/N. Pass
    end_visit=False to fill without finishing."""
    tab_index = payload.get("tab_index")
    page = await get_active_page(tab_index=tab_index)
    if not await is_flutter_page(page):
        return {"error": "autofill only works on the Novatek Flutter app"}

    widgets = await extract_flutter_widgets(page)
    if not _autopilot_visit_ok(widgets):
        return {"error": "Not on the Visit Mode screen — the autopilot is restricted to visit mode. "
                         "Start the visit first."}

    report = {"forms": [], "progress": _autopilot_progress(widgets)}
    attempted = set()  # form labels already worked on this run — never re-target
    # "Fill whatever is open" is only safe AFTER a verified submit, when Novatek
    # has auto-selected the next unsubmitted form. On the very first pass the
    # open form is whatever the user happened to leave selected, which may
    # already be complete — that is how a finished form got re-filled and
    # re-submitted. So the first target always comes from the sidebar scan,
    # which knows which forms carry the checkmark.
    trust_open_form = False
    visit_date_done = False   # the visit date belongs to the visit, not a form

    for _ in range(int(payload.get("max_forms", 40))):
        # Re-establish the ground truth every iteration. Visit Mode was checked
        # once at the start and never again, so a modal opened mid-walk left the
        # walker clicking blind: a participant-queries dialog stayed up across
        # runs and every subsequent "click" landed inside it — including one
        # that looked like the sidebar's Saliva Samples row and one that looked
        # like the visit-date field.
        for _attempt in range(3):
            if not any(w.get("role") == "dialog" for w in widgets):
                break
            how = await _autopilot_clear_dialog(page, widgets)
            report["forms"].append({
                "note": f"cleared a blocking dialog ({how or 'could not close it'})"})
            widgets = await extract_flutter_widgets(page)
            if not how:
                break
        if any(w.get("role") == "dialog" for w in widgets):
            report["forms"].append({
                "error": "a dialog is open and could not be dismissed — stopping rather "
                         "than clicking blind underneath it"})
            break
        if not _autopilot_visit_ok(widgets):
            report["forms"].append({
                "error": "no longer on the Visit Mode screen — stopping. Every click from "
                         "here would land on whatever replaced it."})
            break

        progress = _autopilot_progress(widgets)
        if progress:
            done_n, total_n = (int(x) for x in progress.split("/"))
            if done_n >= total_n:
                break

        # After a verified submission Novatek AUTO-SELECTS the next
        # unsubmitted form. When question blocks are already on screen, fill
        # exactly the form that is open and DO NOT click the sidebar — a
        # manual sidebar click is what used to re-open an already-submitted
        # form and rewrite its answers.
        blocks_open = trust_open_form and bool(
            _autopilot_blocks(_autopilot_panel(widgets, 330)))
        if not blocks_open and trust_open_form:
            for _ in range(8):
                await page.wait_for_timeout(400)
                widgets = await extract_flutter_widgets(page)
                if _autopilot_blocks(_autopilot_panel(widgets, 330)):
                    blocks_open = True
                    break
        if blocks_open:
            target_label = "(auto-selected form)"
            form_res = await cmd_autofill_form(dict(payload, skip_visit_date=visit_date_done))
            visit_date_done = True
            report["forms"].append({
                "form": target_label,
                "status": form_res.get("status", form_res.get("error")),
                "answered": len(form_res.get("answered", [])),
                "unresolved": form_res.get("unresolved", []),
                "progress_after": form_res.get("progress_after"),
            })
            # Let the auto-selection of the next form settle before re-reading.
            await page.wait_for_timeout(1000)
            widgets = await extract_flutter_widgets(page)
            new_progress = _autopilot_progress(widgets)
            if new_progress == progress and not form_res.get("submit_verified"):
                report["forms"].append({"note": "open form did not advance progress — stopping for review"})
                break
            continue

        # FALLBACK ONLY (no form auto-selected): screenshot the sidebar strip
        # and target the first form WITHOUT the green checkmark. Forms do NOT
        # complete in list order, and the semantics tree shows checked and
        # unchecked buttons identically — the checkmark exists only as
        # pixels, so this scan is what keeps an already-submitted form from
        # ever being re-selected.
        target = None
        inner_h = await page.evaluate("() => window.innerHeight")
        for _scroll_try in range(6):
            sidebar = [w for w in widgets if w.get("role") == "button"
                       and w["bounds"]["center_x"] < 330 and w.get("label")
                       and not any(r in w["label"].lower() for r in _AUTOPILOT_SIDEBAR_RESERVED)
                       and w["bounds"]["center_y"] > 250
                       and w["bounds"]["center_y"] < inner_h - 20]
            sidebar.sort(key=lambda w: w["bounds"]["center_y"])
            if sidebar:
                checks = await _sidebar_checkmarks(page, sidebar)
                # Report what the scan actually saw. Whether a form counts as
                # already submitted decides if it gets re-answered, so this is
                # not incidental logging — when the scan is wrong the run
                # rewrites finished work, and there was no way to see that from
                # the outside.
                report["sidebar"] = [
                    {"form": (b.get("label") or "")[:48], "checked": bool(c),
                     "bounds": {k: b["bounds"].get(k) for k in ("x", "width", "center_y")}}
                    for b, c in zip(sidebar, checks)]
                open_forms = [b for b, c in zip(sidebar, checks)
                              if not c and (b.get("label") or "") not in attempted]
                if open_forms:
                    target = open_forms[0]
                    break
            # Every on-screen form is checked (or already attempted) but the
            # counter says more remain — scroll the sidebar list for the rest.
            await page.mouse.move(160, inner_h // 2)
            await page.mouse.wheel(0, 400)
            await page.wait_for_timeout(350)
            widgets = await extract_flutter_widgets(page)
        if target is None:
            report["forms"].append({"error": "no unchecked form found in the sidebar despite incomplete progress"})
            break
        await _autopilot_click(page, target, settle=1000)
        # Give the form time to render its first questions.
        for _ in range(10):
            widgets = await extract_flutter_widgets(page)
            if _autopilot_blocks(_autopilot_panel(widgets, 330)):
                break
            await page.wait_for_timeout(400)

        attempted.add(target.get("label") or "")
        trust_open_form = True   # from here on Novatek drives the selection
        form_res = await cmd_autofill_form(dict(payload, skip_visit_date=visit_date_done))
        visit_date_done = True   # visit-level: set once, never per form
        report["forms"].append({
            "form": target.get("label"),
            "status": form_res.get("status", form_res.get("error")),
            "answered": len(form_res.get("answered", [])),
            "unresolved": form_res.get("unresolved", []),
            "progress_after": form_res.get("progress_after"),
        })

        widgets = await extract_flutter_widgets(page)
        new_progress = _autopilot_progress(widgets)
        if new_progress == progress and not form_res.get("submit_verified"):
            report["forms"].append({"note": f"'{target.get('label')}' did not advance progress — stopping for review"})
            break

    widgets = await extract_flutter_widgets(page)
    report["progress"] = _autopilot_progress(widgets)
    parts = (report["progress"] or "0/1").split("/")
    report["all_forms_submitted"] = len(parts) == 2 and parts[0] == parts[1]

    # THE FLOW: every form filled and submitted, the counter verifying it, then
    # End Visit. Gated on the counter alone — never on how the run "felt" it
    # went. Pass end_visit=False to fill without finishing.
    if report["all_forms_submitted"] and payload.get("end_visit", True):
        if await _autopilot_fill_visit_date(page, widgets, time.strftime("%-m/%-d/%Y"),
                                            report, 330):
            widgets = await extract_flutter_widgets(page)
        await _autopilot_end_visit(page, 330, report)
    report["status"] = "visit_filled" if report["all_forms_submitted"] else "visit_incomplete"
    # A blunt sentence, not a status code. The per-form entries each say
    # "autofill_done", and a model summarising this report will happily read a
    # handful of those as "the visit is finished" — one did exactly that and
    # offered to End Visit on a visit sitting at 0/18 with nothing submitted.
    # The counter is the only authority, so it is stated in words that leave no
    # room to narrate around.
    counter = report.get("progress") or "unknown"
    if report["all_forms_submitted"]:
        ended = report.get("end_visit") or {}
        if ended.get("confirmed"):
            tail = " The visit has been ended."
        elif ended.get("clicked"):
            tail = f" End Visit was clicked but not confirmed: {ended.get('reason', '')}"
        else:
            tail = f" The visit was NOT ended: {ended.get('reason', 'end_visit was disabled')}"
        report["verdict"] = (
            f"COMPLETE - the progress counter reads {counter}. Every form is submitted."
            + tail)
    else:
        remaining = "unknown"
        try:
            # `parts` are the strings either side of the "/" — they are compared
            # as strings above, so they must be converted before arithmetic.
            remaining = str(int(parts[1]) - int(parts[0]))
        except (ValueError, IndexError, TypeError):
            pass
        report["verdict"] = (
            f"NOT COMPLETE - the progress counter reads {counter}, so {remaining} form(s) "
            "are still unsubmitted. Do NOT say the visit is finished, do NOT say every "
            "form has a checkmark, and do NOT offer to End Visit. Report the counter as "
            "it stands and say which forms remain.")
    report["widgets"] = _slim_widgets(widgets)
    return report


async def cmd_takeover_participant(payload):
    """TAKE OVER a participant: from the participant profile screen, starts
    the next available visit (named in the Visit Progress card), completes
    every form via the visit autopilot, enters the Actual visit date, executes
    End Visit, returns to the profile, and repeats for the following visit —
    until no next visit remains, max_visits is reached, or a visit cannot be
    completed. Reports per-visit results."""
    tab_index = payload.get("tab_index")
    max_visits = int(payload.get("max_visits", 5))
    today = time.strftime("%-m/%-d/%Y")

    page = await get_active_page(tab_index=tab_index)
    if not await is_flutter_page(page):
        return {"error": "takeover only works on the Novatek Flutter app"}

    report = {"visits": [], "status": "takeover_incomplete"}
    profile_url = page.url

    for _visit_no in range(max_visits):
        widgets = await extract_flutter_widgets(page)

        if not _autopilot_visit_ok(widgets):
            if not _autopilot_profile_ok(widgets):
                report["visits"].append({"error": "not on a participant profile or visit-mode screen"})
                break
            profile_url = page.url
            nxt = next((w for w in widgets
                        if (w.get("label") or "").strip().startswith("Next:")), None)
            if not nxt:
                report["status"] = "no_next_visit"
                break
            visit_name = nxt["label"].split("Next:", 1)[1].strip()
            start_btn = next(
                (w for w in widgets if w.get("role") == "button" and w.get("label")
                 and (visit_name.lower() in w["label"].lower()
                      or "start" in w["label"].lower() and "visit" in w["label"].lower())), None)
            if start_btn is None:
                # The start affordance is often the "Next: …" row itself.
                start_btn = nxt
            await _autopilot_click(page, start_btn, settle=1200)
            await _autopilot_accept_dialog(page, extra_labels=("start", "start visit"))
            await page.wait_for_timeout(1200)
            widgets = await extract_flutter_widgets(page)
            if not _autopilot_visit_ok(widgets):
                report["visits"].append({"visit": visit_name,
                                         "error": "could not enter visit mode from the profile"})
                break
        else:
            visit_name = "(visit already in progress)"

        visit_res = await cmd_autofill_visit(dict(payload))
        entry = {"visit": visit_name,
                 "progress": visit_res.get("progress"),
                 "forms": visit_res.get("forms", []),
                 "ended": False}

        if not visit_res.get("all_forms_submitted"):
            entry["error"] = "visit forms incomplete — stopping for review"
            report["visits"].append(entry)
            break

        # Actual visit date, then End Visit.
        widgets = await extract_flutter_widgets(page)
        date_box = next((w for w in widgets if w.get("role") == "textbox"
                         and "actual" in (w.get("label") or "").lower()), None)
        if date_box is not None and not date_box.get("value"):
            await _autopilot_type(page, date_box, today)
            await _autopilot_accept_dialog(page)  # in case a picker opened
            entry["actual_date"] = today
        widgets = await extract_flutter_widgets(page)
        end_btn = next((w for w in widgets
                        if "end visit" in (w.get("label") or "").lower()
                        and w.get("role") in ("button", "widget")), None)
        if end_btn is None:
            entry["error"] = "End Visit button not found"
            report["visits"].append(entry)
            break
        await _autopilot_click(page, end_btn, settle=1000)
        await _autopilot_accept_dialog(page, extra_labels=("end visit", "end"))
        await page.wait_for_timeout(2000)
        entry["ended"] = True
        report["visits"].append(entry)

        # Back to the profile for the next visit.
        widgets = await extract_flutter_widgets(page)
        if not _autopilot_profile_ok(widgets):
            await cmd_open({"url": profile_url, "tab_index": tab_index})
            await page.wait_for_timeout(2500)

    ended = sum(1 for v in report["visits"] if v.get("ended"))
    if report["status"] != "no_next_visit":
        report["status"] = "takeover_done" if ended and all(
            v.get("ended") for v in report["visits"]) else "takeover_incomplete"
    report["visits_completed"] = ended
    return report


async def cmd_batch(payload):
    """Executes a sequence of actions in one HTTP request — the speed path for
    form filling. Each LLM round-trip costs 10-25s; before this command a
    one-question form burned 6+ of them (read, click, type, submit, verify…).
    Now the model plans a whole screenful once and the daemon executes every
    step back-to-back at machine speed, returning a fresh widget list in the
    same response so no separate re-read round is needed either."""
    actions = payload.get("actions") or []
    return_widgets = payload.get("return_widgets", True)
    tab_index = payload.get("tab_index")

    results = []
    for i, act in enumerate(actions):
        cmd = str(act.get("cmd") or "").strip().lstrip("/")
        path = f"/{cmd}"
        handler = COMMANDS.get(path)
        if handler is None or path == "/batch":
            results.append({"index": i, "cmd": cmd, "ok": False, "error": "unknown batch action"})
            continue
        sub = {k: v for k, v in act.items() if k != "cmd"}
        if tab_index is not None and "tab_index" not in sub:
            sub["tab_index"] = tab_index
        try:
            res = await handler(sub)
        except Exception as e:
            res = {"error": str(e)}
        entry = {"index": i, "cmd": cmd, "ok": "error" not in res}
        if not entry["ok"]:
            entry["error"] = str(res.get("error"))
        results.append(entry)
        # A failed click/type usually means stale coordinates; later actions in
        # the plan would then hit the wrong targets, so stop and let the model
        # re-plan from the fresh widget list below.
        if not entry["ok"] and cmd in ("flutter_click", "flutter_type", "click", "type"):
            results.append({"note": f"stopped after failed action {i}; re-plan remaining actions from the returned widgets"})
            break

    out = {
        "status": "batch_done",
        "requested": len(actions),
        "executed": sum(1 for r in results if "cmd" in r),
        "results": results,
    }
    if return_widgets:
        try:
            page = await get_active_page(tab_index=tab_index)
            if await is_flutter_page(page):
                out["widgets"] = _slim_widgets(await extract_flutter_widgets(page))
                out["coordinate_usage"] = "click/type with target 'flutter:coords(x,y)'"
            out["url"] = page.url
        except Exception as e:
            out["widgets_error"] = str(e)
    return out


COMMANDS = {
    "/open": cmd_open,
    "/batch": cmd_batch,
    "/autofill_form": cmd_autofill_form,
    "/autofill_visit": cmd_autofill_visit,
    "/takeover_participant": cmd_takeover_participant,
    "/close": cmd_close,
    "/list": cmd_list,
    "/switch_tab": cmd_switch_tab,
    "/list_profiles": cmd_list_profiles,
    "/detect_app_type": cmd_detect_app_type,
    "/flutter_widgets": cmd_flutter_widgets,
    "/flutter_click": cmd_flutter_click,
    "/flutter_type": cmd_flutter_type,
    "/flutter_run_test": cmd_flutter_run_test,
    "/click": cmd_click,
    "/type": cmd_type,
    "/press_key": cmd_press_key,
    "/scroll": cmd_scroll,
    "/content": cmd_get_content,
    "/screenshot": cmd_screenshot,
    "/upload_file": cmd_upload_file,
}


def _send_json_response(writer, data, status=200):
    status_text = {
        200: "OK",
        404: "Not Found",
        408: "Request Timeout",
        500: "Internal Server Error",
    }.get(status, "OK")
    body = json.dumps(data).encode("utf-8")
    header = (
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("utf-8")
    writer.write(header + body)


async def handle_client(reader, writer):
    """Handles an HTTP client request over asyncio streams."""
    try:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            await writer.wait_closed()
            return

        line = request_line.decode("utf-8", errors="ignore").strip()
        parts = line.split()
        if len(parts) < 2:
            writer.close()
            await writer.wait_closed()
            return
        method, path = parts[0], parts[1]

        headers = {}
        while True:
            header_line = await reader.readline()
            if not header_line or header_line in (b"\r\n", b"\n"):
                break
            h_text = header_line.decode("utf-8", errors="ignore").strip()
            if ":" in h_text:
                k, v = h_text.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        content_length = int(headers.get("content-length", 0))
        body_bytes = await reader.readexactly(content_length) if content_length > 0 else b""

        if method == "GET" and path == "/health":
            _send_json_response(writer, {"ok": True}, 200)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        cmd = COMMANDS.get(path)
        if cmd is None or method != "POST":
            _send_json_response(writer, {"error": "unknown endpoint"}, 404)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return

        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            payload = {}

        try:
            result = await cmd(payload)
            _send_json_response(writer, result, 200)
        except PlaywrightTimeoutError as e:
            print(f"[browser-daemon] {path} timed out: {e}", file=sys.stderr)
            _send_json_response(writer, {"error": f"Action timed out: {e}"}, 408)
        except PlaywrightError as e:
            err_str = str(e)
            print(f"[browser-daemon] {path} Playwright error: {err_str}", file=sys.stderr)
            if "closed" in err_str.lower() or "session closed" in err_str.lower():
                state["context"] = None
                state["browser"] = None
                state["pw"] = None
                _clean_orphaned_playwright_processes()
                _send_json_response(writer, {"error": f"Browser was closed: {err_str}"}, 500)
            else:
                _send_json_response(writer, {"error": f"Browser action failed: {err_str}"}, 500)
        except Exception as e:
            print(f"[browser-daemon] {path} unexpected error: {e}", file=sys.stderr)
            _send_json_response(writer, {"error": f"Internal browser daemon error: {e}"}, 500)

        await writer.drain()
        writer.close()
        await writer.wait_closed()
    except Exception:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle_client, "127.0.0.1", PORT)
    print(f"[browser-daemon] listening on 127.0.0.1:{PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
