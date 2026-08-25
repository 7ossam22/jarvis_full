// js/view/hud.js — the top-left note-count readout (View layer).
const noteCountEl = document.getElementById("note-count");

export function setNoteCount(n) {
  noteCountEl.textContent = `${n} notes indexed`;
}
