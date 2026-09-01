// js/view/systemPanel.js — the bottom-left "system state" panel (View layer).
//
// Answers "what is going on right now, and what is wrong" — which the app
// could not answer before. A provider that fell over, a tool that returned an
// error, a run that quietly stalled: all of it existed only as stderr lines in
// a terminal, so from the browser a broken run and a working one looked
// identical. The console panel opposite shows the CONVERSATION; this one shows
// the SYSTEM underneath it.
//
// Polls GET /status. Read-only: nothing here drives the app.
import { log as logLine } from "./console.js";

const POLL_MS = 2000;

// Errors are announced into the conversation log once each, so a failure is
// visible even when this panel is collapsed or ignored.
const announced = new Set();

let el = null;
let collapsed = false;
let lastErrorCount = 0;

function build() {
  el = document.createElement("div");
  el.id = "system-panel";
  el.innerHTML = `
    <div id="system-panel-header">
      <span id="system-panel-title">SYSTEM</span>
      <span id="system-panel-state">…</span>
      <span id="system-panel-toggle" title="Collapse/expand">▾</span>
    </div>
    <div id="system-panel-body">
      <div id="system-panel-problems"></div>
      <div id="system-panel-facts"></div>
      <div id="system-panel-events"></div>
    </div>`;
  document.body.appendChild(el);
  el.querySelector("#system-panel-toggle").addEventListener("click", () => {
    collapsed = !collapsed;
    el.classList.toggle("collapsed", collapsed);
    el.querySelector("#system-panel-toggle").textContent = collapsed ? "▸" : "▾";
  });
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function secs(n) {
  return n >= 60 ? `${Math.floor(n / 60)}m ${Math.round(n % 60)}s` : `${Math.round(n)}s`;
}

/** One-line summary in the header: the thing a glance should answer. */
function stateOf(s) {
  if (s.stuck) {
    const worst = s.running.reduce((a, b) => (b.seconds > a.seconds ? b : a), s.running[0]);
    return { text: `STUCK · ${secs(worst.seconds)}`, cls: "bad" };
  }
  if (s.problems && s.problems.length) return { text: "NEEDS ATTENTION", cls: "warn" };
  if (s.busy) {
    const worst = s.running.reduce((a, b) => (b.seconds > a.seconds ? b : a), s.running[0]);
    return { text: `WORKING · ${secs(worst.seconds)}`, cls: "busy" };
  }
  if (s.error_count) return { text: `IDLE · ${s.error_count} error(s)`, cls: "warn" };
  return { text: "IDLE", cls: "ok" };
}

function render(s) {
  const state = stateOf(s);
  const stateEl = el.querySelector("#system-panel-state");
  stateEl.textContent = state.text;
  stateEl.className = state.cls;
  el.classList.toggle("alert", state.cls === "bad" || state.cls === "warn");

  el.querySelector("#system-panel-problems").innerHTML =
    (s.problems || []).map((p) => `<div class="sp-problem">${esc(p)}</div>`).join("");

  const running = (s.running || [])
    .map((r) => `<div class="sp-run${r.stuck ? " stuck" : ""}">▸ ${esc(r.what)} · ${secs(r.seconds)}${r.stuck ? " · not responding" : ""}</div>`)
    .join("");

  const session = (s.sessions || [])[0];
  const facts = [
    `brain: ${esc(s.provider_in_use || "none")}${s.model ? ` (${esc(s.model)})` : ""}`,
    `tools: ${s.tools}`,
    session ? `context: ${session.turns} turns, ${(session.chars / 1000).toFixed(1)}k chars${session.full ? " — FULL" : ""}` : "context: empty",
  ].map((f) => `<div class="sp-fact">${f}</div>`).join("");

  el.querySelector("#system-panel-facts").innerHTML = running + facts;

  el.querySelector("#system-panel-events").innerHTML = (s.events || [])
    .slice(0, 8)
    .map((e) => `<div class="sp-event ${esc(e.kind)}"><span class="sp-ago">${secs(e.ago)} ago</span>${esc(e.message)}${e.detail ? `<span class="sp-detail">${esc(e.detail)}</span>` : ""}</div>`)
    .join("") || `<div class="sp-event info">nothing to report</div>`;
}

/** Surface new errors in the conversation log too — a panel nobody is looking
 *  at is not a report. */
function announce(s) {
  for (const e of s.events || []) {
    if (e.kind !== "error") continue;
    const key = `${e.message}|${Math.round(e.at || 0)}`;
    if (announced.has(key)) continue;
    announced.add(key);
    logLine(`${e.message}${e.detail ? ` — ${e.detail}` : ""}`, "error");
  }
  if (s.stuck && s.error_count !== lastErrorCount) {
    lastErrorCount = s.error_count;
  }
}

async function poll() {
  try {
    const res = await fetch("/status");
    if (res.ok) {
      const s = await res.json();
      render(s);
      announce(s);
    }
  } catch (e) {
    // The server itself is unreachable — that IS the status.
    if (el) {
      const stateEl = el.querySelector("#system-panel-state");
      stateEl.textContent = "SERVER UNREACHABLE";
      stateEl.className = "bad";
      el.classList.add("alert");
    }
  }
}

export function startSystemPanel() {
  if (!el) build();
  poll();
  setInterval(poll, POLL_MS);
}
