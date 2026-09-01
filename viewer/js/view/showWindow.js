// js/view/showWindow.js — the large embedded page viewer (View layer).
// "Show me X" opens the page in an iframe covering ~3/4 of the screen.
//
// Many sites refuse to be framed (X-Frame-Options: DENY, or a CSP
// frame-ancestors policy) and Chrome then paints a blank broken-document box.
// That refusal is invisible from here — a cross-origin iframe reports nothing
// readable whether it loaded, refused, or is still loading — so this module
// asks the server first (GET /embeddable), which has no such restriction and
// can simply read the headers. Three outcomes:
//
//   yes      iframe it, as before.
//   no       show the page's text instead, when the server could fetch it,
//            with a prominent "open in a tab" action. No more blank box.
//   unknown  the server's own probe was blocked (Cloudflare answers 403 to
//            anything that isn't a real browser) — still try the iframe, since
//            the browser often succeeds where the probe cannot, but arm a
//            watchdog so a silent failure still ends in the fallback.
let el = null;
let currentUrl = "";
let watchdog = null;

// How long to let an iframe prove itself when the server could not tell.
const UNKNOWN_LOAD_GRACE_MS = 6000;

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
    <iframe id="show-window-frame" allow="autoplay; encrypted-media; picture-in-picture; fullscreen"></iframe>
    <div id="show-window-fallback" hidden></div>`;
  document.body.appendChild(el);
  el.querySelector("#show-window-close").addEventListener("click", closeShowWindow);
}

function showFrame() {
  el.querySelector("#show-window-frame").hidden = false;
  el.querySelector("#show-window-fallback").hidden = true;
}

/** Replaces the iframe with readable text (or an explanation when the page
 *  could not be fetched either). `readable` may be null. */
function showFallback(url, reason, readable) {
  const frame = el.querySelector("#show-window-frame");
  frame.src = "about:blank";
  frame.hidden = true;

  const box = el.querySelector("#show-window-fallback");
  const safe = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const body = readable && readable.content
    ? `<h2 class="sw-fb-title">${safe(readable.title || url)}</h2>
       <pre class="sw-fb-text">${safe(readable.content)}</pre>`
    : `<p class="sw-fb-empty">The page could not be read from here either — it may be
         behind a bot check or rendered entirely in the browser.</p>`;

  box.innerHTML = `
    <div class="sw-fb-note">
      This site does not allow being embedded${reason ? ` (${safe(reason)})` : ""}.
      ${readable && readable.content ? "Showing its text instead." : ""}
      <a class="sw-fb-open" href="${safe(url)}" target="_blank" rel="noopener">Open in a tab ↗</a>
    </div>
    ${body}`;
  box.hidden = false;
  box.scrollTop = 0;
}

export async function openShowWindow(url) {
  if (!el) build();
  currentUrl = url;
  clearTimeout(watchdog);

  let display = url;
  try { display = new URL(url).hostname.replace(/^www\./, ""); } catch (e) {}
  el.querySelector("#show-window-title").textContent = display.toUpperCase();
  el.querySelector("#show-window-newtab").href = url;
  el.querySelector("#show-window-fallback").hidden = true;
  el.classList.add("open");

  let verdict = { embeddable: "unknown", reason: "", readable: null };
  try {
    const res = await fetch(`/embeddable?url=${encodeURIComponent(url)}`);
    if (res.ok) verdict = await res.json();
  } catch (e) {
    // Probe failed locally — fall through to trying the iframe.
  }
  // A later "show me X" may have replaced this one while we were waiting.
  if (currentUrl !== url || !isShowWindowOpen()) return;

  if (verdict.embeddable === "no") {
    showFallback(url, verdict.reason, verdict.readable);
    return;
  }

  const frame = el.querySelector("#show-window-frame");
  showFrame();
  frame.src = url;

  if (verdict.embeddable === "unknown") {
    // The probe was blocked (Cloudflare and friends answer 403 to anything
    // that is not a real browser), so the frame may well work. It may also be
    // showing Chrome's blank refusal box, and nothing readable from here tells
    // the two apart: a framed error page fires `load` just like a real one.
    //
    // So do NOT tear down a frame that might be fine. After a grace period,
    // overlay a hint that explains the blank box if that is what the user is
    // looking at, and offers a way out. Harmless if the page loaded.
    watchdog = setTimeout(() => {
      if (currentUrl === url && isShowWindowOpen()) showHint(url);
    }, UNKNOWN_LOAD_GRACE_MS);
  }
}

/** A dismissible banner over the iframe, for when we could not verify whether
 *  the page can be framed. Explains the blank box without destroying a frame
 *  that may have loaded correctly. */
function showHint(url) {
  let hint = el.querySelector("#show-window-hint");
  if (!hint) {
    hint = document.createElement("div");
    hint.id = "show-window-hint";
    el.appendChild(hint);
  }
  hint.innerHTML = `
    <span>Blank? This site may refuse to be embedded.</span>
    <button type="button" class="sw-hint-read">Read it here</button>
    <a class="sw-hint-open" href="${url.replace(/"/g, "&quot;")}" target="_blank" rel="noopener">Open in a tab ↗</a>
    <span class="sw-hint-close" title="Dismiss">✕</span>`;
  hint.hidden = false;

  hint.querySelector(".sw-hint-close").onclick = () => { hint.hidden = true; };
  hint.querySelector(".sw-hint-read").onclick = async () => {
    hint.querySelector(".sw-hint-read").textContent = "Reading…";
    let readable = null;
    try {
      const res = await fetch(`/embeddable?url=${encodeURIComponent(url)}`);
      if (res.ok) readable = (await res.json()).readable;
    } catch (e) {}
    if (currentUrl !== url || !isShowWindowOpen()) return;
    hint.hidden = true;
    showFallback(url, "", readable);
  };
}

export function closeShowWindow() {
  if (!el) return;
  clearTimeout(watchdog);
  currentUrl = "";
  el.classList.remove("open");
  // Drop the src so background audio/video in the framed page actually stops.
  el.querySelector("#show-window-frame").src = "about:blank";
}

export function isShowWindowOpen() {
  return !!el && el.classList.contains("open");
}
