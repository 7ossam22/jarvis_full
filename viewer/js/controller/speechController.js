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
import { log as logLine } from "../view/console.js";

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
let currentWasListening = false;

export function isSpeaking() {
  return !!currentAudio || ("speechSynthesis" in window && speechSynthesis.speaking);
}

export function stopSpeaking() {
  if (currentAudio) {
    try {
      currentAudio.pause();
      currentAudio.currentTime = 0;
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

export async function speak(text, options = {}) {
  if (!text) return;
  if (isSpeaking()) {
    stopSpeaking();
  }

  const wasListening = onSpeechStart();
  currentWasListening = wasListening;

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
    logLine("Speaking (ElevenLabs voice).", "mic");
  } catch (err) {
    logLine("ElevenLabs unavailable — falling back to browser voice.", "error");
    speakWithBrowserVoice(text, wasListening);
  }
}

