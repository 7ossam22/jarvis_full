# Novatek Web Application

The **Novatek Web Application** is an enterprise clinical trial and medical operations portal automated and managed through JARVIS.

## Application Architecture & Type

- **Deployments**: there is more than one. `nec` -> `https://nec-dev.autotrial.app`,
  `hcc` -> `https://hcc-dev.autotrial.app`. The authoritative list is `novatek.sites`
  in `config.json`; these are separate live trial sites and must never be confused.
- **Application Framework**: Built with **Flutter Web** (rendered via CanvasKit/HTML5 canvas with an interactive accessibility semantics tree).
- **Automation Engine**: Automated on the desktop via [[Playwright Browser Control]] using `browser_open_url`, `browser_detect_app_type`, `browser_flutter_get_widgets`, `browser_flutter_type`, and `browser_flutter_click`.

## Login & Navigation Flow Specifications

When instructed to open Novatek (e.g. "open Novatek", "open Novatek hcc", "open Novatek nec",
"launch Novatek portal", "log into Novatek"):

1. **Launch & URL Navigation**:
   - Use the `novatek_open` tool with the site the user named ("open Novatek hcc" ->
     `site: "hcc"`), which resolves the URL from configuration. Omit `site` only when the
     user named none. Never type a portal URL from memory.
2. **App Type Inspection & Semantics Detection**:
   - Inspect the page using `browser_detect_app_type` to confirm the application framework is **Flutter Web** with CanvasKit rendering.
3. **Screen & Loading State Verification**:
   - Verify that the application opens on the **Login Screen**.
   - Check if a loading indicator animation (spinner/progress bar) is currently active.
   - If no loading animation is happening, it indicates that the system is ready and waiting for user credentials to be entered.
4. **Credential Entry & Submission**:
   - **Admin Username**: from `novatek.username` in `config.json` (or `NOVATEK_USERNAME`)
   - **Admin Password**: from `novatek.password` in `config.json` (or `NOVATEK_PASSWORD`)
   - Type the credentials into the corresponding Flutter input fields (`browser_flutter_type` or `browser_type` on `Username`/`Password`).
   - Click the **Login** button (`browser_flutter_click` or `browser_click`).
5. **Dashboard Verification**:
   - Upon successful authentication, confirm that the application navigates smoothly to the **Dashboard Screen**.

## Related Systems

- [[Novatek Form Automation]] for automated question handling, data entry rules, and form submission.
- [[Playwright Browser Control]] for executing Chrome automation and Flutter Web canvas interactions.
- [[Zen White Glassmorphic UI]] for displaying browser viewports and screenshots.
- [[Linux System Controller]] for system-level desktop automation and window management.
