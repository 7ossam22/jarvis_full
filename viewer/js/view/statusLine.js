// js/view/statusLine.js — the small status text above the dock (View layer).
const statusEl = document.getElementById("status-line");

export function setStatus(text) {
  statusEl.textContent = text;
}
