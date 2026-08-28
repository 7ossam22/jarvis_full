# Playwright Browser Control

The **Playwright Browser Control** connector provides full, interactive automation of Google Chrome and Chromium on the local desktop.

## Key Capabilities

- **Window & Tab Reuse** (`browser_open_url`, `browser_list_tabs`): Navigates inside the active controllable window without opening unmanaged duplicate windows.
- **Chrome Persistent Profiles** (`browser_list_profiles`): Automatically discovers and switches between real Chrome profiles (e.g. *Hossam*, *Doxx*, *Habiba*, *Elkenany*) preserving logins, cookies, bookmarks, and extensions in conflict-free persistent directories (`~/.config/jarvis-chrome/`).
- **Interactive Web Navigation** (`browser_click`, `browser_type`, `browser_press_key`, `browser_scroll`): Interacts with web pages by clicking elements, typing into input fields, sending keystrokes, and scrolling.
- **DOM & Content Extraction** (`browser_get_content`): Extracts clean text, HTML, or interactive DOM elements (`button`, `a`, `input`) with selector hints.
- **Browser Screenshots** (`browser_screenshot`): Captures full-page or viewport PNG screenshots and displays them in the 3/4-screen screenshot reference viewer.

## Architecture

Runs as a dedicated daemon (`tools/browser_daemon.py`) using Playwright with anti-automation stealth flags (`--disable-blink-features=AutomationControlled`) and self-healing context lifecycles.

## Related Systems

- [[Zen White Glassmorphic UI]] for displaying the 3/4-screen screenshot reference viewer.
- [[Discord Connector]] for uploading captured browser screenshots.
- [[Linux System Controller]] for system-level desktop automation.
