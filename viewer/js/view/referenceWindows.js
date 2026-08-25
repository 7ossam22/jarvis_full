// js/view/referenceWindows.js — floating image lookup results (View layer):
// one, or a gallery of several at once. Each lands at its own random spot on
// screen and they all stay there until dismissed (voice/text command or a
// ✕) or replaced as a set by the next lookup that turns up images.
const referenceLayer = document.getElementById("reference-layer");
let referenceWindows = []; // [{ el }]

// Recognized as a dismiss command without a round trip to Claude — instant,
// and doesn't burn an API call on "close that" / "dismiss those".
const DISMISS_RE = /^(dismiss|close|hide|remove|clear)\b.*\b(that|those|it|them|image|images|picture|pictures|photo|photos|reference|window|windows|gallery)\b|^(dismiss|close|hide)\s+(that|it|those|them)$/i;

export function isDismissCommand(text) {
  return DISMISS_RE.test(text);
}

export function hasOpenReferences() {
  return referenceWindows.length > 0;
}

// Places `count` windows into a shuffled grid of cells across the safe area
// (avoiding the hud/status strip and the dock/toast strip) rather than pure
// independent random — with several windows on screen at once, unconstrained
// randomness tends to stack them on top of each other.
function planReferenceSlots(count) {
  const margin = 24, topDead = 90, bottomDead = 130;
  const areaW = Math.max(200, window.innerWidth - margin * 2);
  const areaH = Math.max(200, window.innerHeight - topDead - bottomDead);
  const cols = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / cols);
  const cellW = areaW / cols, cellH = areaH / rows;
  const cells = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) cells.push({ r, c });
  for (let i = cells.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [cells[i], cells[j]] = [cells[j], cells[i]];
  }
  return cells.map(cell => ({
    left: margin + cell.c * cellW + Math.random() * Math.max(0, cellW - 300) * 0.7,
    top: topDead + cell.r * cellH + Math.random() * Math.max(0, cellH - 220) * 0.7,
  }));
}

function placeReferenceWindows() {
  const slots = planReferenceSlots(referenceWindows.length || 1);
  referenceWindows.forEach((w, i) => {
    const slot = slots[i % slots.length];
    const el = w.el;
    const ew = el.offsetWidth || 300, eh = el.offsetHeight || 220;
    const left = Math.min(slot.left, window.innerWidth - ew - 24);
    const top = Math.min(slot.top, window.innerHeight - eh - 130);
    el.style.left = `${Math.round(Math.max(24, left))}px`;
    el.style.top = `${Math.round(Math.max(90, top))}px`;
  });
}

function removeReferenceWindow(entry) {
  entry.el.classList.remove("show");
  setTimeout(() => entry.el.remove(), 400);
  referenceWindows = referenceWindows.filter(w => w !== entry);
}

export function clearReferences() {
  referenceWindows.slice().forEach(removeReferenceWindow);
}

function createReferenceWindow(url) {
  const el = document.createElement("div");
  el.className = "reference-window";
  el.style.pointerEvents = "auto";
  const closeBtn = document.createElement("span");
  closeBtn.className = "ref-close";
  closeBtn.title = "Dismiss";
  closeBtn.textContent = "✕";
  const img = document.createElement("img");
  img.alt = "Lookup reference";
  el.append(closeBtn, img);
  referenceLayer.appendChild(el);

  const entry = { el };
  closeBtn.addEventListener("click", () => removeReferenceWindow(entry));
  img.onerror = () => removeReferenceWindow(entry); // bad/dead URL — don't show a broken frame
  img.onload = placeReferenceWindows; // re-layout once real size is known
  img.src = url;
  referenceWindows.push(entry);
  placeReferenceWindows();
  requestAnimationFrame(() => el.classList.add("show"));
}

// Replaces the current set of reference windows with a fresh one — a new
// lookup's images are a new "batch", not additions to the old batch.
export function showReferences(urls) {
  if (!urls || !urls.length) return;
  clearReferences();
  urls.forEach(createReferenceWindow);
}
