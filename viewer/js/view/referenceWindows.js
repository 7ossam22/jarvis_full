// js/view/referenceWindows.js — floating image & video lookup results (View layer):
// Displays floating image cards and video player cards in safe screen areas.
const referenceLayer = document.getElementById("reference-layer");
let referenceWindows = []; // [{ el }]

const DISMISS_RE = /^(dismiss|close|hide|remove|clear)\b.*\b(that|those|it|them|image|images|picture|pictures|photo|photos|video|videos|clip|clips|movie|movies|reference|window|windows|gallery)\b|^(dismiss|close|hide)\s+(that|it|those|them)$/i;

export function isDismissCommand(text) {
  return DISMISS_RE.test(text);
}

export function hasOpenReferences() {
  return referenceWindows.length > 0;
}

function planReferenceSlots(count) {
  const margin = 24, topDead = 90, bottomDead = 140;
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
    left: margin + cell.c * cellW + Math.random() * Math.max(0, cellW - 320) * 0.7,
    top: topDead + cell.r * cellH + Math.random() * Math.max(0, cellH - 240) * 0.7,
  }));
}

function placeReferenceWindows() {
  const slots = planReferenceSlots(referenceWindows.length || 1);
  referenceWindows.forEach((w, i) => {
    const slot = slots[i % slots.length];
    const el = w.el;
    const ew = el.offsetWidth || 340, eh = el.offsetHeight || 240;
    const left = Math.min(slot.left, window.innerWidth - ew - 24);
    const top = Math.min(slot.top, window.innerHeight - eh - 140);
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
  el.className = "reference-window image-ref";
  el.style.pointerEvents = "auto";
  const closeBtn = document.createElement("span");
  closeBtn.className = "ref-close";
  closeBtn.title = "Dismiss";
  closeBtn.textContent = "✕";
  const img = document.createElement("img");
  img.alt = "Lookup reference image";
  el.append(closeBtn, img);
  referenceLayer.appendChild(el);

  const entry = { el };
  closeBtn.addEventListener("click", () => removeReferenceWindow(entry));
  img.onerror = () => removeReferenceWindow(entry);
  img.onload = placeReferenceWindows;
  img.src = url;
  referenceWindows.push(entry);
  placeReferenceWindows();
  requestAnimationFrame(() => el.classList.add("show"));
}

function createVideoReferenceWindow(url) {
  const el = document.createElement("div");
  el.className = "reference-window video-ref";
  el.style.pointerEvents = "auto";

  const header = document.createElement("div");
  header.className = "ref-header";
  header.innerHTML = `<span class="ref-tag">🎥 VIDEO REFERENCE</span>`;

  const closeBtn = document.createElement("span");
  closeBtn.className = "ref-close";
  closeBtn.title = "Dismiss";
  closeBtn.textContent = "✕";

  header.appendChild(closeBtn);
  el.appendChild(header);

  const ytMatch = url.match(/(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/\s]{11})/i);
  if (ytMatch) {
    const iframe = document.createElement("iframe");
    iframe.src = `https://www.youtube.com/embed/${ytMatch[1]}?autoplay=1`;
    iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
    iframe.allowFullscreen = true;
    iframe.className = "ref-video-frame";
    el.appendChild(iframe);
  } else {
    const video = document.createElement("video");
    video.controls = true;
    video.autoplay = true;
    video.loop = true;
    video.playsInline = true;
    video.className = "ref-video-player";
    video.src = url;
    video.onerror = () => removeReferenceWindow({ el });
    video.onloadeddata = placeReferenceWindows;
    el.appendChild(video);
  }

  referenceLayer.appendChild(el);
  const entry = { el };
  closeBtn.addEventListener("click", () => removeReferenceWindow(entry));
  referenceWindows.push(entry);
  placeReferenceWindows();
  requestAnimationFrame(() => el.classList.add("show"));
}

export function showReferences(imageUrls = [], videoUrls = []) {
  const imgs = Array.isArray(imageUrls) ? imageUrls : [];
  const vids = Array.isArray(videoUrls) ? videoUrls : [];
  if (!imgs.length && !vids.length) return;
  clearReferences();
  imgs.forEach(createReferenceWindow);
  vids.forEach(createVideoReferenceWindow);
}

