// js/main.js — boot sequence (Controller layer: bootstrap). Fetches config,
// wires all the controllers/views together, and unlocks audio + starts
// listening once the user clicks "Wake JARVIS" (browsers block audio
// autoplay until a real user gesture).
import { fetchConfig } from "./model/config.js";
import { graphData } from "./model/graphData.js";
import { setNoteCount } from "./view/hud.js";
import { showAnswer } from "./view/toast.js";
import * as scene from "./view/scene.js";
import { speak, setMicHooks } from "./controller/speechController.js";
import { initConsole, log as logLine } from "./view/console.js";
import {
  initVoiceController, pauseMic, resumeMic, enableListening, hasRecognizer,
} from "./controller/voiceController.js";
import { handleSubmit, cancelPendingRequests } from "./controller/chatController.js";

async function boot() {
  initConsole();
  const config = await fetchConfig();
  logLine("Config loaded from server.", "system");

  scene.initScene(config);
  setNoteCount(graphData.nodes.length);
  logLine(`${graphData.nodes.length} notes indexed.`, "system");

  // Break the voiceController <-> speechController circular dependency via
  // injection: speechController needs to pause/resume the mic around
  // playback, voiceController needs to speak() its own acknowledgments —
  // neither module imports the other directly.
  setMicHooks({ pauseMic, resumeMic });
  initVoiceController(config, handleSubmit, cancelPendingRequests);

  document.getElementById("wake-btn").addEventListener("click", () => {
    document.getElementById("boot").style.display = "none";
    logLine("JARVIS woken.", "system");
    const addressTerm = config?.persona?.address_term || "sir";
    const greeting = `Good evening, ${addressTerm}. ${graphData.nodes.length} notes indexed, all present and accounted for.`;
    showAnswer(greeting);
    speak(greeting);

    if (hasRecognizer()) {
      enableListening();
      logLine("Always-listening mode enabled.", "mic");
      // speak()'s completion starts the recognizer once the greeting finishes
      // (via the mic hooks wired above); this fallback only covers browsers
      // where speechSynthesis fires no events at all.
      setTimeout(() => resumeMic(true), 4000);
    }
  });
}

boot();
