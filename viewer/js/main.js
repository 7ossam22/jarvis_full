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
import { startSystemPanel } from "./view/systemPanel.js";
import { startSpotifyPanel } from "./view/spotifyPanel.js";
import { setHttpsPort } from "./view/cameraWindow.js";

async function boot() {
  initConsole();

  let loadedConfig = null;

  const wakeBtn = document.getElementById("wake-btn");
  if (wakeBtn) {
    wakeBtn.addEventListener("click", () => {
      const bootEl = document.getElementById("boot");
      if (bootEl) bootEl.style.display = "none";
      logLine("JARVIS woken.", "system");
      const addressTerm = loadedConfig?.persona?.address_term || "sir";
      const count = graphData?.nodes?.length ?? 0;
      const greeting = `Good evening, ${addressTerm}. ${count} notes indexed, all present and accounted for.`;
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

  try {
    loadedConfig = await fetchConfig();
    logLine("Config loaded from server.", "system");
    if (loadedConfig?.server?.https_enabled) setHttpsPort(loadedConfig.server.https_port);
  } catch (err) {
    console.error("[jarvis] Config load error:", err);
  }

  try {
    scene.initScene(loadedConfig);
  } catch (err) {
    console.error("[jarvis] Scene init error:", err);
  }

  try {
    setNoteCount(graphData.nodes.length);
    logLine(`${graphData.nodes.length} notes indexed.`, "system");
  } catch (err) {
    console.error("[jarvis] Set note count error:", err);
  }

  // Break the voiceController <-> speechController circular dependency via
  // injection: speechController needs to pause/resume the mic around
  // playback, voiceController needs to speak() its own acknowledgments —
  // neither module imports the other directly.
  try {
    setMicHooks({ pauseMic, resumeMic });
    initVoiceController(loadedConfig, handleSubmit, cancelPendingRequests);
  } catch (err) {
    console.error("[jarvis] Voice controller init error:", err);
  }
}

boot().catch((err) => {
  console.error("[jarvis] Boot failure:", err);
});

// Live system diagnostics, bottom-left: what is running, what is stuck, and
// every error the server recorded rather than only printing to stderr.
try {
  startSystemPanel();
} catch (err) {
  console.error("[jarvis] System panel error:", err);
}

// Now-playing panel above it: what Spotify is doing, on which device, with
// transport controls that go through the same connector JARVIS uses.
try {
  startSpotifyPanel();
} catch (err) {
  console.error("[jarvis] Spotify panel error:", err);
}
