#!/usr/bin/env bash
# tools/setup_browser.sh — one-time setup for JARVIS's browser control.
# Creates .venv-browser with Playwright and downloads its Chromium build.
# No sudo needed; everything lands in the project dir and ~/.cache/ms-playwright.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv-browser
.venv-browser/bin/pip install --quiet playwright
.venv-browser/bin/playwright install chromium
echo "Done. JARVIS will start the browser daemon automatically when needed."
