// js/view/panel.js — the side panel showing a clicked note's details (View layer).
const panelEl = document.getElementById("panel");

export function openPanel(node) {
  document.getElementById("panel-group").textContent = node.group;
  document.getElementById("panel-title").textContent = node.label;
  document.getElementById("panel-excerpt").textContent = node.excerpt;
  panelEl.classList.add("open");
}

export function closePanel() {
  panelEl.classList.remove("open");
}

document.getElementById("panel-close").addEventListener("click", closePanel);
