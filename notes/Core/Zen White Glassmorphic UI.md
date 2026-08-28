# Zen White Glassmorphic UI

The **Zen White Glassmorphic UI** is the visual frontend of JARVIS, blending Japanese Zen minimalism with modern frosted glassmorphism.

## Visual Design Elements

- **Pearlescent Zen Background**: Subtle radial gradient with etched Japanese geometric patterns.
- **White Frosted Glass Panels**: Deep blur (`backdrop-filter: blur(32px)`), translucent white surfaces (`rgba(255, 255, 255, 0.88)`), and soft drop shadows.
- **Sumi-Ink Typography & Accents**: High-contrast dark charcoal text (`#0f172a`), Akane crimson (`#e11d48`) and Amber (`#f59e0b`) indicators.
- **Audio Spectrum Waveform**: Real-time canvas visualizer rendering voice energy and TTS playback waveforms.

## Integrated Workspace Viewers

1. **Jira Interactive Deck (`jiraDeck.js`)**:
   - 4-column Kanban board (**TO DO**, **IN PROGRESS**, **UNDER REVIEW**, **DONE**).
   - Interactive issue drawer with live status transitions and comment composer.
2. **3/4 Screen Screenshot Reference Viewer (`screenshotViewer.js`)**:
   - Displays captured desktop and browser screenshots occupying 75% of the screen (`75vw × 75vh`) with click-to-zoom, save, and full-resolution viewing.
3. **Show Window (`showWindow.js`)**:
   - Embedded web previewer for web links and external references.

## Related Systems

- [[Atlassian Jira Connector]] powering the Jira Kanban Deck.
- [[Playwright Browser Control]] and [[Linux System Controller]] feeding the screenshot viewer.
- [[Neural Cortex 3D Graph]] rendered inside the 3D canvas viewport.
