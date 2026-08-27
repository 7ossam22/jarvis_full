// js/view/screenshotViewer.js — Large 3/4 screen Screenshot Reference Viewer (View layer).
// Displays high-resolution screenshots taking 3/4 of the system viewport with
// Zen White Glassmorphic framing, zoom toggle, and quick actions.

let el = null;
let currentUrl = null;

function build() {
  el = document.createElement("div");
  el.id = "screenshot-viewer";
  el.className = "screenshot-viewer hidden";
  el.innerHTML = `
    <div class="screenshot-glass-card">
      <div class="screenshot-header">
        <div class="screenshot-header-left">
          <span class="screenshot-icon">📸</span>
          <span class="screenshot-title">SCREENSHOT REFERENCE</span>
          <span class="screenshot-badge" id="screenshot-filename">desktop_capture.png</span>
        </div>
        <div class="screenshot-header-right">
          <a class="screenshot-btn" id="screenshot-open-tab" href="#" target="_blank" rel="noopener" title="Open original image">↗ Open Full</a>
          <a class="screenshot-btn" id="screenshot-download" href="#" download="screenshot.png" title="Download image">⬇ Save</a>
          <button class="screenshot-btn screenshot-btn-close" id="screenshot-close" title="Dismiss (or say 'dismiss')">✕</button>
        </div>
      </div>
      <div class="screenshot-body">
        <div class="screenshot-img-wrap" id="screenshot-wrap">
          <img id="screenshot-img" src="" alt="Captured system screenshot" />
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(el);

  el.querySelector("#screenshot-close").addEventListener("click", closeScreenshot);
  
  // Click on image container to toggle fit vs actual size zoom
  const wrap = el.querySelector("#screenshot-wrap");
  wrap.addEventListener("click", () => {
    wrap.classList.toggle("zoomed");
  });
}

export function openScreenshot(url, title) {
  if (!el) build();
  currentUrl = url;

  const filename = title || url.split("/").pop() || "screenshot.png";
  el.querySelector("#screenshot-filename").textContent = filename;
  el.querySelector("#screenshot-open-tab").href = url;
  el.querySelector("#screenshot-download").href = url;
  el.querySelector("#screenshot-download").download = filename;

  const img = el.querySelector("#screenshot-img");
  img.src = url;

  const wrap = el.querySelector("#screenshot-wrap");
  wrap.classList.remove("zoomed");

  el.classList.remove("hidden");
}

export function closeScreenshot() {
  if (!el) return;
  el.classList.add("hidden");
}

export function isScreenshotOpen() {
  return !!el && !el.classList.contains("hidden");
}
