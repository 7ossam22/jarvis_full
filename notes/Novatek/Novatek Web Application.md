# Novatek Web Application

The **Novatek Web Application** (`https://nec-dev.autotrial.app`) is an enterprise clinical trial and medical operations portal automated and managed through JARVIS.

## Application Architecture & Type

- **Host URL**: `https://nec-dev.autotrial.app`
- **Application Framework**: Built with **Flutter Web** (rendered via CanvasKit/HTML5 canvas with an interactive accessibility semantics tree).
- **Automation Engine**: Automated on the desktop via [[Playwright Browser Control]] using `browser_open_url`, `browser_detect_app_type`, `browser_flutter_get_widgets`, `browser_flutter_type`, and `browser_flutter_click`.

## Login & Navigation Flow Specifications

When instructed to open Novatek (e.g. "open Novatek", "launch Novatek portal", "log into Novatek"):

1. **Launch & URL Navigation**:
   - Open `https://nec-dev.autotrial.app` in Google Chrome using `browser_open_url`.
2. **App Type Inspection & Semantics Detection**:
   - Inspect the page using `browser_detect_app_type` to confirm the application framework is **Flutter Web** with CanvasKit rendering.
3. **Screen & Loading State Verification**:
   - Verify that the application opens on the **Login Screen**.
   - Check if a loading indicator animation (spinner/progress bar) is currently active.
   - If no loading animation is happening, it indicates that the system is ready and waiting for user credentials to be entered.
4. **Credential Entry & Submission**:
   - **Admin Username**: `Admin`
   - **Admin Password**: `nursenurse123`
   - Type the credentials into the corresponding Flutter input fields (`browser_flutter_type` or `browser_type` on `Username`/`Password`).
   - Click the **Login** button (`browser_flutter_click` or `browser_click`).
5. **Dashboard Verification**:
   - Upon successful authentication, confirm that the application navigates smoothly to the **Dashboard Screen**.

## Related Systems

- [[Novatek Form Automation]] for automated question handling, data entry rules, and form submission.
- [[Playwright Browser Control]] for executing Chrome automation and Flutter Web canvas interactions.
- [[Zen White Glassmorphic UI]] for displaying browser viewports and screenshots.
- [[Linux System Controller]] for system-level desktop automation and window management.
