// js/view/showWindow.js — the large embedded page viewer (View layer).
// "Show me X" opens the page in an iframe covering ~3/4 of the screen.
// Some sites refuse to be iframed (X-Frame-Options/CSP) — that can't be
// detected reliably cross-origin, so the header always offers an
// "open in tab ↗" escape hatch.
let el = null;

function build() {
  el = document.createElement("div");
  el.id = "show-window";
  el.innerHTML = `
    <div id="show-window-header">
      <span id="show-window-title">VIEWER</span>
      <span id="show-window-actions">
        <a id="show-window-newtab" href="#" target="_blank" rel="noopener" title="Open in a real tab">↗</a>
        <span id="show-window-close" title="Dismiss">✕</span>
      </span>
    </div>
    <iframe id="show-window-frame" allow="autoplay; encrypted-media; picture-in-picture; fullscreen"></iframe>`;
  document.body.appendChild(el);
  el.querySelector("#show-window-close").addEventListener("click", closeShowWindow);
}

export function openShowWindow(url) {
  if (!el) build();
  let display = url;
  try { display = new URL(url).hostname.replace(/^www\./, ""); } catch (e) {}
  el.querySelector("#show-window-title").textContent = display.toUpperCase();
  el.querySelector("#show-window-newtab").href = url;
  el.querySelector("#show-window-frame").src = url;
  el.classList.add("open");
}

export function closeShowWindow() {
  if (!el) return;
  el.classList.remove("open");
  // Drop the src so background audio/video in the framed page actually stops.
  el.querySelector("#show-window-frame").src = "about:blank";
}

export function isShowWindowOpen() {
  return !!el && el.classList.contains("open");
}
