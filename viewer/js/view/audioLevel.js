// js/view/audioLevel.js — a single, shared "how loud is JARVIS right now"
// signal (View layer). The orb deforms to real speech energy rather than a
// canned sine wave, so it needs the actual samples coming out of the TTS
// audio element. Web Audio can only tap an element once, so every element we
// have ever wired is remembered and reused.
//
// The browser's built-in SpeechSynthesis voice exposes no audio node at all.
// For that path only, synthetic() drives a plausible speech envelope so the
// orb still moves instead of sitting frozen mid-sentence.

let ctx = null;
let analyser = null;
let bins = null;
const tapped = new WeakSet();

let level = 0;          // smoothed 0..1 loudness
let synthetic = false;  // true while the fallback voice is speaking
let syntheticSince = 0;

function ensureContext() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.75;
  bins = new Uint8Array(analyser.frequencyBinCount);
  analyser.connect(ctx.destination);
  return ctx;
}

// Route one <audio> element through the analyser. Safe to call repeatedly:
// an element already tapped is ignored, which is what the per-chunk playback
// in speechController needs.
export function attachAudioElement(el) {
  try {
    if (!el || tapped.has(el)) return;
    if (!ensureContext()) return;
    if (ctx.state === "suspended") ctx.resume();
    ctx.createMediaElementSource(el).connect(analyser);
    tapped.add(el);
  } catch (_) {
    // Cross-origin or already-connected element: fall back to the envelope.
    useSyntheticLevel(true);
  }
}

export function useSyntheticLevel(on) {
  synthetic = !!on;
  if (synthetic) syntheticSince = performance.now();
}

// 0..1, already smoothed. Called once per animation frame by the orb.
export function speechLevel() {
  let raw = 0;
  if (analyser) {
    analyser.getByteFrequencyData(bins);
    // Voice lives low in the spectrum; weighting there keeps hiss from
    // reading as loudness.
    let sum = 0, weight = 0;
    const usable = Math.floor(bins.length * 0.45);
    for (let i = 0; i < usable; i++) {
      const w = 1 - i / usable;
      sum += bins[i] * w;
      weight += w;
    }
    raw = weight ? sum / weight / 255 : 0;
  }
  if (raw < 0.02 && synthetic) {
    const t = (performance.now() - syntheticSince) / 1000;
    raw = 0.25 + 0.22 * Math.sin(t * 9.1) + 0.15 * Math.sin(t * 3.3 + 1.2);
    raw = Math.max(0, raw);
  }
  // Asymmetric smoothing: snap up on a syllable, ease down after it.
  const k = raw > level ? 0.45 : 0.09;
  level += (raw - level) * k;
  return Math.min(1, level);
}
