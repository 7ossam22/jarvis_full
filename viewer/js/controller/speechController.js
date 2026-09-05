// js/controller/speechController.js — turns text into speech (Controller
// layer): the configured TTS backend via the server proxy (any API key stays
// server-side), falling back to the browser's built-in voice if none is
// configured or the call fails. Also the single choke point for pausing/
// resuming the mic and the whole-brain glow/answer-toast lifecycle around a
// spoken reply — every speak() path (real voice or fallback) routes through
// onSpeechStart/onSpeechEnd, so "stays until he finishes speaking" is
// automatic.
//
// Long replies are spoken a piece at a time. Synthesis cost is linear in the
// length of the text, so asking for a whole paragraph at once meant the first
// word landed seconds late — a 800-character answer took ~18s of silence
// before anything played. Splitting the reply and pipelining the requests
// puts the first sound at roughly the cost of the first sentence (~1s) no
// matter how long the answer is, because the rest is synthesized while the
// opening plays.
//
// Mic pause/resume is injected via setMicHooks() rather than imported
// directly from voiceController.js — that module calls back into speak()
// (for "Yes, sir?" / closing lines), so a static import in both directions
// would be circular. main.js wires the two together at boot.
import { speakRequest } from "../model/api.js";
import { hideAnswer } from "../view/toast.js";
import { startBrainGlow, stopBrainGlow } from "../view/scene.js";
import { log as logLine } from "../view/console.js";

// The opening piece gates time-to-first-word, so keep it short. Later pieces
// are synthesized while earlier ones play, so they can be bigger — fewer
// requests, and more context for the voice to phrase naturally.
const FIRST_CHUNK_CHARS = 120;
const CHUNK_CHARS = 240;
const HARD_SPLIT_CHARS = 320;  // a run-on sentence still has to be broken up
const GROWTH = 1.8;            // how much bigger each piece may be than the last
const PREFETCH = 2;            // requests kept in flight ahead of playback

let micHooks = { pauseMic: () => false, resumeMic: () => {} };
export function setMicHooks(hooks) {
  micHooks = hooks;
}

let britishVoice = null;
function pickVoice() {
  const voices = speechSynthesis.getVoices();
  britishVoice = voices.find(v => /en-GB/i.test(v.lang)) || voices.find(v => /british/i.test(v.name)) || voices[0] || null;
}
speechSynthesis.onvoiceschanged = pickVoice;
pickVoice();

function onSpeechStart() {
  const wasListening = micHooks.pauseMic();
  startBrainGlow();
  return wasListening;
}

function onSpeechEnd(wasListening) {
  speakingActive = false;
  micHooks.resumeMic(wasListening);
  stopBrainGlow();
  hideAnswer();
}

function speakWithBrowserVoice(text, wasListening) {
  if (!("speechSynthesis" in window)) { onSpeechEnd(wasListening); return; }
  speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  if (britishVoice) utter.voice = britishVoice;
  utter.rate = 1.0;
  utter.pitch = 0.95;
  utter.onend = utter.onerror = () => onSpeechEnd(wasListening);
  speechSynthesis.speak(utter);
}

// Periods that end an abbreviation rather than a sentence. Splitting on them
// would be audible: the pieces are rejoined for playback, so "3 p.m." spoken
// as two pieces becomes "3 p. m.".
const ABBREVIATIONS = new Set([
  "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
  "approx", "dept", "inc", "ltd", "no", "fig", "al", "am", "pm",
]);

/** Offsets in `source` where one sentence ends and the next begins. */
function sentenceBounds(source) {
  const bounds = [];
  const re = /([.!?…]+["'”’)\]]*)(\s+)|(\n+)/g;
  let m;
  while ((m = re.exec(source)) !== null) {
    if (m[3]) { bounds.push(m.index + m[0].length); continue; }  // newline always breaks
    if (m[1] === ".") {
      const word = (source.slice(0, m.index).match(/[A-Za-z]+$/) || [""])[0].toLowerCase();
      // A lone letter is an initial ("J. Smith") or half of "p.m." / "e.g.".
      if (word.length === 1 || ABBREVIATIONS.has(word)) continue;
    }
    bounds.push(m.index + m[0].length);
  }
  return bounds;
}

/** Breaks text into speakable pieces at sentence boundaries.
 *
 * Exported to be testable on its own — nothing else imports it. Works in
 * offsets and slices the original string rather than splitting and rejoining,
 * so the text handed to the voice is always a verbatim substring of the
 * reply: no whitespace invented, none dropped. Sentences are regrouped up to
 * a character budget so an answer made of four-word sentences doesn't become
 * four HTTP requests, and any single sentence over the budget is broken at a
 * comma (or failing that a space), so one run-on can't reintroduce the very
 * delay this exists to avoid. */
export function splitForSpeech(text) {
  const source = (text || "").trim();
  if (!source) return [];

  // Sentences, as [start, end) ranges covering the whole string end to end.
  const pieces = [];
  let cursor = 0;
  for (const bound of [...sentenceBounds(source), source.length]) {
    if (bound > cursor) { pieces.push([cursor, bound]); cursor = bound; }
  }

  // Break up anything still too long to synthesize promptly.
  const sized = [];
  for (let [begin, end] of pieces) {
    while (end - begin > HARD_SPLIT_CHARS) {
      const limit = begin + HARD_SPLIT_CHARS;
      let cut = source.lastIndexOf(", ", limit);
      if (cut - begin < HARD_SPLIT_CHARS / 2) cut = source.lastIndexOf(" ", limit);
      // Neither a clause break nor a space in range: one enormous unbroken
      // token, so take the budget as-is rather than looping forever.
      if (cut - begin < HARD_SPLIT_CHARS / 2) cut = limit;
      else cut += 1;  // keep the comma or space with the piece it closes
      sized.push([begin, cut]);
      begin = cut;
    }
    if (end > begin) sized.push([begin, end]);
  }

  // Regroup. Ranges stay contiguous, so extending one is just moving its end.
  //
  // Budgets grow as they go. The opener is kept short because it alone
  // decides when the reply starts speaking, but that buys a problem: a short
  // opener yields little audio, and if the next piece is much bigger its
  // synthesis outlasts the opener's playback and the voice stalls mid-reply.
  // Synthesis runs ~2.8x faster than real time, so a piece can afford to be
  // ~1.8x its predecessor and still be ready before the predecessor finishes
  // playing. Growing by that factor ramps up to full-size pieces within a
  // sentence or two without ever letting playback catch up with the encoder.
  const ranges = [];
  for (const [begin, end] of sized) {
    const idx = ranges.length - 1;
    const prev = idx > 0 ? ranges[idx - 1] : null;
    const budget = idx <= 0
      ? FIRST_CHUNK_CHARS
      : Math.min(CHUNK_CHARS,
                 Math.max(FIRST_CHUNK_CHARS, Math.round(GROWTH * (prev[1] - prev[0]))));
    if (idx >= 0 && end - ranges[idx][0] <= budget) ranges[idx][1] = end;
    else ranges.push([begin, end]);
  }

  return ranges.map(([begin, end]) => source.slice(begin, end).trim()).filter(Boolean);
}

let currentAudio = null;
let currentWasListening = false;
let speakingActive = false;
// Bumped by every new speak() and by stopSpeaking(), so a pipeline whose turn
// has passed can notice mid-flight and drop its remaining audio on the floor.
let speakToken = 0;

export function isSpeaking() {
  // speakingActive matters between pieces: currentAudio is briefly null while
  // the next one is awaited, and a reply is still very much in progress then.
  return speakingActive || !!currentAudio ||
    ("speechSynthesis" in window && speechSynthesis.speaking);
}

export function stopSpeaking() {
  speakToken++;  // orphan any in-flight pipeline
  speakingActive = false;
  if (currentAudio) {
    try {
      currentAudio.pause();
      currentAudio.currentTime = 0;
      URL.revokeObjectURL(currentAudio.src);
    } catch (e) {}
    currentAudio = null;
  }
  if ("speechSynthesis" in window) {
    try { speechSynthesis.cancel(); } catch (e) {}
  }
  clearTimeout(speak._safety);
  stopBrainGlow();
  hideAnswer();
  logLine("Speech interrupted by user.", "mic");
  micHooks.resumeMic(currentWasListening);
}

/** Plays one blob to completion. Resolves when it ends, rejects if it can't
 * play at all — the caller decides whether that means falling back. */
function playPiece(blob, token) {
  return new Promise((resolve, reject) => {
    if (token !== speakToken) { resolve(); return; }
    const audio = new Audio(URL.createObjectURL(blob));
    currentAudio = audio;
    const done = (fn) => () => {
      try { URL.revokeObjectURL(audio.src); } catch (e) {}
      if (currentAudio === audio) currentAudio = null;
      fn();
    };
    audio.onended = done(resolve);
    audio.onerror = done(() => reject(new Error("audio playback failed")));
    audio.play().catch(done(() => reject(new Error("audio play() rejected"))));
  });
}

// The old single 45s guard assumed one request for the whole reply; a long
// answer legitimately outlives it now, so it is re-armed per piece instead.
function armSafety(wasListening, token) {
  clearTimeout(speak._safety);
  speak._safety = setTimeout(() => {
    if (token !== speakToken) return;
    speakToken++;
    onSpeechEnd(wasListening);
  }, 45000);
}

export async function speak(text, options = {}) {
  if (!text) return;
  if (isSpeaking()) {
    stopSpeaking();
  }

  const wasListening = onSpeechStart();
  currentWasListening = wasListening;
  speakingActive = true;
  const token = ++speakToken;

  const chunks = splitForSpeech(text);
  const pending = new Array(chunks.length).fill(null);
  const request = (i) => {
    if (i >= chunks.length || pending[i]) return;
    const p = speakRequest(chunks[i]);
    // A pipeline that gets orphaned never awaits these; swallow the rejection
    // here so it doesn't surface as an unhandled promise rejection.
    p.catch(() => {});
    pending[i] = p;
  };

  armSafety(wasListening, token);

  let i = 0;
  let spokeAnything = false;
  try {
    for (; i < chunks.length; i++) {
      for (let j = i; j <= i + PREFETCH; j++) request(j);
      const blob = await pending[i];
      if (token !== speakToken) return;
      armSafety(wasListening, token);
      await playPiece(blob, token);
      if (token !== speakToken) return;
      if (!spokeAnything) {
        spokeAnything = true;
        logLine(`Speaking (${chunks.length} segment${chunks.length > 1 ? "s" : ""}).`, "mic");
      }
    }
    clearTimeout(speak._safety);
    onSpeechEnd(wasListening);
  } catch (err) {
    if (token !== speakToken) return;
    clearTimeout(speak._safety);
    // Whatever is left unsaid still gets said — a backend that dies halfway
    // through shouldn't swallow the back half of the answer.
    const remaining = chunks.slice(i).join(" ");
    logLine(spokeAnything
      ? "Voice failed mid-reply — finishing in the browser voice."
      : "Voice backend unavailable — falling back to browser voice.", "error");
    speakWithBrowserVoice(remaining, wasListening);
  }
}
