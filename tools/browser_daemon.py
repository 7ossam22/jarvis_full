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

from playwright.async_api import (
    async_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

PORT = 4701
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

state = {
    "pw": None,
    "browser": None,
    "context": None,
    "current_profile": None,
    "active_tab_index": 0,
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
            return
        try:
            await state["context"].new_page()
            state["active_tab_index"] = 0
            return
        except Exception:
            state["context"] = None

    if state["context"] is not None and state["current_profile"] != req_profile_name:
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

    context = None
    try:
        context = await state["pw"].chromium.launch_persistent_context(
            user_data_dir=req_dir,
            channel="chrome",
            headless=False,
            args=launch_args,
            no_viewport=True,
            ignore_https_errors=True,
        )
    except Exception as e:
        print(f"[browser-daemon] Chrome channel launch failed ({e}); cleaning locks and trying Chromium…", file=sys.stderr)
        _clean_profile_locks(req_dir)
        try:
            context = await state["pw"].chromium.launch_persistent_context(
                user_data_dir=req_dir,
                headless=False,
                args=launch_args,
                no_viewport=True,
                ignore_https_errors=True,
            )
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
        await page.evaluate("""() => {
            const ph = document.querySelector('flt-semantics-placeholder, [role="button"][aria-label*="accessibility" i]');
            if (ph) {
                ph.click();
            }
            try {
                window.dispatchEvent(new Event('flutter-semantics-update'));
            } catch (e) {}
            const host = document.querySelector('flt-semantics-host');
            if (host) {
                host.focus();
            }
        }""")
        await page.wait_for_timeout(350)
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


async def find_flutter_widget_coords(page, target):
    """Finds exact viewport (center_x, center_y) coordinates for a Flutter widget target."""
    await enable_flutter_semantics(page)
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
        "widgets": widgets,
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

    await page.wait_for_timeout(350)
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
    await page.wait_for_timeout(250)

    if clear:
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")

    await page.keyboard.type(text, delay=20)
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
            await page.wait_for_timeout(300)
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
    await page.wait_for_timeout(300)

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
            await page.wait_for_timeout(250)
            if clear:
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(text, delay=20)
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
    await page.wait_for_timeout(300)

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

    if is_flt:
        # Move mouse over the target scrollable container
        await page.mouse.move(target_x, target_y)
        await page.wait_for_timeout(50)

        if delta_y != 0:
            # 1. Dispatch wheel event directly on the form container
            await page.mouse.wheel(0, delta_y)
            await page.wait_for_timeout(200)

            # 2. Synthetic touch drag to ensure Flutter ScrollController advances
            try:
                drag_dist = min(abs(delta_y), 300)
                drag_y_end = target_y - drag_dist if direction == "down" else target_y + drag_dist
                await page.mouse.move(target_x, target_y)
                await page.mouse.down()
                await page.mouse.move(target_x, drag_y_end, steps=8)
                await page.mouse.up()
                await page.wait_for_timeout(200)
            except Exception:
                pass

        if direction == "top":
            await page.keyboard.press("Home")
            await page.mouse.wheel(0, -3000)
        elif direction == "bottom":
            await page.keyboard.press("End")
            await page.mouse.wheel(0, 3000)

        # Refresh semantics tree so newly revealed widgets are indexed
        await enable_flutter_semantics(page)
    else:
        if delta_y != 0:
            await page.mouse.wheel(0, delta_y)
            await page.wait_for_timeout(200)

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
            "elements": flutter_widgets,
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
                    if any(kw in lbl for kw in ("upload", "signed consent", "file", "attach", "browse", "choose", "select", "consent")):
                        candidates.append(w.get("label"))

            for target in candidates:
                if not target:
                    continue
                cx, cy, matched_label = await find_flutter_widget_coords(page, target)
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


COMMANDS = {
    "/open": cmd_open,
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
