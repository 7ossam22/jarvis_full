// js/view/cameraWindow.js — the interface's eyes (View layer).
//
// "Look at me" / "what do you see" means the camera on the device showing this
// page, which only the browser can reach — the server has no path to it. So
// the decision to open the camera is made here, before the message is sent,
// and a still frame rides along with it. The alternative (the model asking for
// a picture mid-turn) would need the server to call back into the browser,
// which it cannot do.
//
// Once open, the eyes STAY open: every following message carries a fresh
// frame, so "what am I holding?" needs no ceremony of its own. "Close your
// eyes" shuts it off, and so does the visible close button — a camera you
// cannot obviously turn off is not one people trust.

const PREVIEW_W = 320;

// The largest edge sent to the model. A 1280px frame is ~10x the bytes of a
// 640px one and buys no extra accuracy for "what is in front of me" — it only
// slows the turn and, on a metered key, costs more.
const SEND_MAX_EDGE = 640;
const JPEG_QUALITY = 0.72;

let stream = null;
let videoEl = null;
let panelEl = null;
let lastError = "";

// The server's https port, from /config — so the fix below can name the exact
// URL to open rather than telling the user to go and set https up themselves.
let httpsPort = 0;

export function setHttpsPort(port) {
  httpsPort = Number(port) || 0;
}

/** Camera access needs a secure context. Served over plain http:// on a LAN
 *  address the browser blocks it outright and getUserMedia is not even
 *  defined, which otherwise surfaces as a baffling silent failure. */
export function cameraBlockedReason() {
  if (window.isSecureContext) return "";
  const fix = httpsPort
    ? `Open Jarvis at https://${location.hostname}:${httpsPort} instead and accept the `
      + `self-signed certificate warning once.`
    : `Open Jarvis at http://localhost:${location.port} on the machine itself, or serve it over https.`;
  return `the page is served over http:// from ${location.hostname}, and browsers only allow `
       + `camera access on localhost or https. ${fix}`;
}

export function eyesAreOpen() {
  return !!stream;
}

export function lastCameraError() {
  return lastError;
}

function build() {
  panelEl = document.createElement("div");
  panelEl.id = "camera-window";
  panelEl.innerHTML = `
    <div id="camera-window-header">
      <span id="camera-window-title">EYES</span>
      <span id="camera-window-close" title="Close the camera">✕</span>
    </div>`;
  videoEl = document.createElement("video");
  videoEl.autoplay = true;
  videoEl.playsInline = true;
  videoEl.muted = true;              // a preview that beeps is a bug report
  videoEl.id = "camera-window-video";
  panelEl.appendChild(videoEl);
  document.body.appendChild(panelEl);
  panelEl.querySelector("#camera-window-close").addEventListener("click", closeEyes);
}

/** Turns the camera on. Returns "" on success, or a reason for a person. */
export async function openEyes() {
  if (stream) return "";
  const blocked = cameraBlockedReason();
  if (blocked) { lastError = blocked; return blocked; }

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  } catch (e) {
    // Name the actual cause: "denied" is the user's own choice to reverse,
    // "not found" is a missing device, and they need different responses.
    lastError = e && e.name === "NotAllowedError"
      ? "camera permission was denied in the browser"
      : e && e.name === "NotFoundError"
        ? "this device has no camera"
        : `the camera could not be opened (${e && e.name ? e.name : e})`;
    stream = null;
    return lastError;
  }

  if (!panelEl) build();
  panelEl.classList.add("open");     // before srcObject: a video laid out at
  videoEl.srcObject = stream;        // zero size can report videoWidth 0
  lastError = "";
  try { await videoEl.play(); } catch { /* autoplay policy; readyState still advances */ }

  // A frame is black — or absent, videoWidth 0 — until the sensor has actually
  // delivered one. Capturing before that sends a picture of nothing, which the
  // model then reports as a broken camera.
  await waitForPixels();
  return "";
}

/** Resolves once the element really has a decoded frame, or after the cap. */
async function waitForPixels(timeoutMs = 4000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (videoEl.readyState >= 2 && videoEl.videoWidth > 0) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  return videoEl.videoWidth > 0;
}

export function closeEyes() {
  if (stream) {
    for (const track of stream.getTracks()) track.stop();
    stream = null;
  }
  if (panelEl) panelEl.classList.remove("open");
  if (videoEl) videoEl.srcObject = null;
}

/** One frame as a data: URL, or null when the eyes are shut.
 *  Waits for pixels if the element is not ready yet — a camera that has just
 *  woken from a background tab reports videoWidth 0 for a moment, and
 *  returning null there is how a sighted turn silently becomes a blind one. */
export async function captureFrame() {
  if (!stream || !videoEl) return null;
  if (!videoEl.videoWidth) await waitForPixels(2000);
  if (!videoEl.videoWidth) {
    lastError = "the camera is open but delivered no pixels";
    return null;
  }
  const scale = Math.min(1, SEND_MAX_EDGE / Math.max(videoEl.videoWidth, videoEl.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(videoEl.videoWidth * scale);
  canvas.height = Math.round(videoEl.videoHeight * scale);
  canvas.getContext("2d").drawImage(videoEl, 0, 0, canvas.width, canvas.height);
  try {
    return canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  } catch {
    return null; // tainted canvas — nothing useful to send
  }
}

export const PREVIEW_WIDTH = PREVIEW_W;
