// js/view/spotifyPanel.js — the "now playing" panel (View layer).
//
// Playback was previously invisible: JARVIS could start a track through the
// Spotify connector, but the browser showed nothing at all, so a silent
// speaker and a wrong-device route looked identical to a working one. This
// panel makes the player state legible — what is playing, on which device, at
// what volume — and gives it transport controls.
//
// Polls GET /spotify/state; commands go to POST /spotify/control, which is a
// thin pass-through to the very same connector functions the model calls, so
// the panel and JARVIS can never disagree about playback.
import { log as logLine } from "./console.js";

const POLL_MS = 4000;

let el = null;
let collapsed = false;
let lastKey = null;      // track identity, so the log announces changes once
let lastErr = null;
let suppressUntil = 0;   // brief poll pause after a command: Spotify's own
                         // state lags the request by a beat, and re-rendering
                         // stale data mid-click makes the panel look broken.

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function clock(ms) {
  const total = Math.max(0, Math.round((ms || 0) / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function build() {
  el = document.createElement("div");
  el.id = "spotify-panel";
  el.style.display = "none";
  el.innerHTML = `
    <div id="spotify-panel-header">
      <span id="spotify-panel-title">NOW PLAYING</span>
      <span id="spotify-panel-state">…</span>
      <span id="spotify-panel-toggle" title="Collapse/expand">▾</span>
    </div>
    <div id="spotify-panel-body">
      <div id="spotify-now">
        <div id="spotify-art"></div>
        <div id="spotify-meta">
          <div id="spotify-track">—</div>
          <div id="spotify-artist"></div>
          <div id="spotify-album"></div>
        </div>
      </div>
      <div id="spotify-progress"><span id="spotify-progress-fill"></span></div>
      <div id="spotify-times"><span id="spotify-elapsed">0:00</span><span id="spotify-duration">0:00</span></div>
      <div id="spotify-controls">
        <button data-action="previous" title="Previous track">⏮</button>
        <button data-action="play" id="spotify-playpause" title="Play / pause">⏵</button>
        <button data-action="next" title="Next track">⏭</button>
        <input id="spotify-volume" type="range" min="0" max="100" value="50" title="Volume" />
        <span id="spotify-volume-val">—</span>
      </div>
      <div id="spotify-device"></div>
    </div>`;
  document.body.appendChild(el);

  el.querySelector("#spotify-panel-toggle").addEventListener("click", () => {
    collapsed = !collapsed;
    el.classList.toggle("collapsed", collapsed);
    el.querySelector("#spotify-panel-toggle").textContent = collapsed ? "▸" : "▾";
  });

  el.querySelectorAll("#spotify-controls button").forEach((btn) => {
    btn.addEventListener("click", () => send(btn.dataset.action));
  });

  // "change", not "input": one command when the user lets go of the slider,
  // rather than one per pixel dragged.
  el.querySelector("#spotify-volume").addEventListener("change", (e) => {
    send("volume", { volume_percent: Number(e.target.value) });
  });
}

/** Send one transport command and report failure honestly rather than
 *  optimistically repainting the panel as if it had worked. */
async function send(action, extra = {}) {
  // The play button is a toggle; ask for the verb that matches what is
  // actually happening right now.
  if (action === "play" && el.dataset.playing === "1") action = "pause";
  try {
    const res = await fetch("/spotify/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra }),
    });
    const out = await res.json();
    if (out.status === "error" || out.status === "unconfigured") {
      logLine(`Spotify ${action} failed — ${out.error || "unknown error"}`, "error");
    } else {
      logLine(`Spotify: ${action}`, "system");
    }
  } catch (e) {
    logLine(`Spotify ${action} failed — ${e}`, "error");
  }
  suppressUntil = Date.now() + 600;
  setTimeout(poll, 700);
}

function setState(text, cls) {
  const s = el.querySelector("#spotify-panel-state");
  s.textContent = text;
  s.className = cls;
  el.classList.toggle("alert", cls === "bad");
}

function render(s) {
  // If the backend has hidden the panel (e.g. initial state or Spotify closed),
  // hide the element completely.
  if (s.status === "hidden" || s.panel_visible === false) {
    el.style.display = "none";
    return;
  }
  el.style.display = "flex";

  const body = el.querySelector("#spotify-panel-body");

  if (s.status === "unconfigured" || s.status === "error") {
    setState(s.status === "unconfigured" ? "NOT CONFIGURED" : "UNREACHABLE", "bad");
    el.querySelector("#spotify-track").textContent = s.error || "Spotify unavailable";
    el.querySelector("#spotify-artist").textContent = "";
    el.querySelector("#spotify-album").textContent = "";
    el.querySelector("#spotify-art").style.backgroundImage = "";
    body.classList.add("dim");
    if (s.error && s.error !== lastErr) {
      lastErr = s.error;
      logLine(`Spotify: ${s.error}`, "error");
    }
    return;
  }
  lastErr = null;
  body.classList.remove("dim");

  if (s.status === "idle") {
    setState("IDLE", "warn");
    el.dataset.playing = "0";
    el.querySelector("#spotify-playpause").textContent = "⏵";
    el.querySelector("#spotify-track").textContent = "Nothing playing";
    el.querySelector("#spotify-artist").textContent = "";
    el.querySelector("#spotify-album").textContent = "";
    el.querySelector("#spotify-art").style.backgroundImage = "";
    el.querySelector("#spotify-progress-fill").style.width = "0%";
    el.querySelector("#spotify-device").textContent = "no active device";
    return;
  }

  const playing = !!s.is_playing;
  el.dataset.playing = playing ? "1" : "0";
  setState(playing ? "PLAYING" : "PAUSED", playing ? "ok" : "warn");
  el.querySelector("#spotify-playpause").textContent = playing ? "⏸" : "⏵";

  el.querySelector("#spotify-track").textContent = s.track || "—";
  el.querySelector("#spotify-artist").textContent = s.artist || "";
  el.querySelector("#spotify-album").textContent = s.album || "";
  el.querySelector("#spotify-art").style.backgroundImage =
    s.art_url ? `url("${encodeURI(s.art_url)}")` : "";

  const pct = s.duration_ms ? Math.min(100, (s.progress_ms / s.duration_ms) * 100) : 0;
  el.querySelector("#spotify-progress-fill").style.width = `${pct}%`;
  el.querySelector("#spotify-elapsed").textContent = clock(s.progress_ms);
  el.querySelector("#spotify-duration").textContent = clock(s.duration_ms);

  if (typeof s.volume_percent === "number") {
    el.querySelector("#spotify-volume").value = s.volume_percent;
    el.querySelector("#spotify-volume-val").textContent = `${s.volume_percent}%`;
  }
  el.querySelector("#spotify-device").innerHTML = s.device
    ? `▸ ${esc(s.device)}${s.device_type ? ` · ${esc(s.device_type)}` : ""}`
    : "no active device";

  // A track change is worth one line in the conversation log; a poll tick is not.
  const key = `${s.track}|${s.artist}`;
  if (playing && key !== lastKey) {
    lastKey = key;
    logLine(`Spotify: ${s.track}${s.artist ? ` — ${s.artist}` : ""}`, "system");
  }
}

async function poll() {
  if (Date.now() < suppressUntil) return;
  try {
    const res = await fetch("/spotify/state");
    if (res.ok) render(await res.json());
  } catch (e) {
    setState("SERVER UNREACHABLE", "bad");
  }
}

export function startSpotifyPanel() {
  if (!el) build();
  poll();
  setInterval(poll, POLL_MS);
}
