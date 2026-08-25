// js/controller/speechController.js — turns text into speech (Controller
// layer): ElevenLabs via the server proxy (key never reaches the browser),
// falling back to the browser's built-in voice if no key is configured or
// the call fails. Also the single choke point for pausing/resuming the mic
// and the whole-brain glow/answer-toast lifecycle around a spoken reply —
// every speak() path (real voice or fallback) routes through onSpeechStart/
// onSpeechEnd, so "stays until he finishes speaking" is automatic.
//
// Mic pause/resume is injected via setMicHooks() rather than imported
// directly from voiceController.js — that module calls back into speak()
// (for "Yes, sir?" / closing lines), so a static import in both directions
// would be circular. main.js wires the two together at boot.
import { speakRequest } from "../model/api.js";
import { hideAnswer } from "../view/toast.js";
import { startBrainGlow, stopBrainGlow } from "../view/scene.js";

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

let currentAudio = null;
export async function speak(text) {
  if (!text) return;
  if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }
  if ("speechSynthesis" in window) speechSynthesis.cancel();

  const wasListening = onSpeechStart();
  // Safety net only — if playback somehow never fires onended/onerror (a stuck
  // stream, a browser quirk), don't leave the mic paused and the toast stuck
  // forever. Harmless if the real completion already fired first.
  clearTimeout(speak._safety);
  speak._safety = setTimeout(() => onSpeechEnd(wasListening), 45000);

  try {
    const blob = await speakRequest(text);
    const audio = new Audio(URL.createObjectURL(blob));
    currentAudio = audio;
    audio.onended = audio.onerror = () => {
      if (currentAudio === audio) currentAudio = null;
      onSpeechEnd(wasListening);
    };
    await audio.play();
  } catch (err) {
    // No ElevenLabs key configured, network hiccup, autoplay block — fall back
    // to the browser's own voice rather than JARVIS going silent.
    speakWithBrowserVoice(text, wasListening);
  }
}
