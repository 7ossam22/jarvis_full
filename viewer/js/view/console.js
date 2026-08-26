// js/view/console.js — the bottom-left "system log" readout (View layer).
// A terminal-style feed of everything happening: mic state, heard speech,
// requests to the server, replies, TTS, and errors. Purely cosmetic/
// diagnostic — no controller reads this module back.
const MAX_LINES = 300;

const root = document.getElementById("console-log");
const body = document.getElementById("console-log-body");
const toggleBtn = document.getElementById("console-log-toggle");

let collapsed = false;

function timestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// kind controls the line's color/prefix — keep these short and scannable,
// like a real log: system boot text, mic/voice activity, what the user said,
// what JARVIS answered, outgoing/incoming server traffic, and errors.
const PREFIX = {
  system: "sys",
  mic: "mic",
  heard: "you",
  reply: "jvs",
  net: "net",
  error: "err",
};

export function log(text, kind = "system") {
  if (!body) return;
  const line = document.createElement("div");
  line.className = `console-line console-${kind}`;
  const tag = PREFIX[kind] || "log";
  line.innerHTML = `<span class="console-ts">${timestamp()}</span><span class="console-tag">${tag}</span><span class="console-msg"></span>`;
  line.querySelector(".console-msg").textContent = text;
  body.appendChild(line);

  while (body.children.length > MAX_LINES) body.removeChild(body.firstChild);
  if (!collapsed) body.scrollTop = body.scrollHeight;
}

export function initConsole() {
  if (!toggleBtn) return;
  toggleBtn.addEventListener("click", () => {
    collapsed = !collapsed;
    root.classList.toggle("collapsed", collapsed);
    if (!collapsed) body.scrollTop = body.scrollHeight;
  });
  log("Neural Core booting…", "system");
}
