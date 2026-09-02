// js/controller/chatController.js — chat/remember orchestration (Controller
// layer): ties model/api.js + view updates + speechController.speak +
// scene camera flights together. Owns the send-button/Enter-key wiring and
// the session ID, and is the target callback voiceController invokes for
// anything heard on the mic (wired by main.js).
import { chatRequest, rememberRequest } from "../model/api.js";
import { graphData, neighborsOf, nodeById } from "../model/graphData.js";
import { showAnswer } from "../view/toast.js";
import { setStatus } from "../view/statusLine.js";
import { setNoteCount } from "../view/hud.js";
import { closePanel } from "../view/panel.js";
import * as referenceWindows from "../view/referenceWindows.js";
import * as scene from "../view/scene.js";
import { speak, stopSpeaking, isSpeaking } from "./speechController.js";
import { standbyStatus } from "./voiceController.js";
import { log as logLine } from "../view/console.js";
import { openShowWindow, closeShowWindow, isShowWindowOpen } from "../view/showWindow.js";
import { renderJiraData, closeJiraDeck, isJiraDeckOpen, openJiraDeck } from "../view/jiraDeck.js";
import { openScreenshot, closeScreenshot, isScreenshotOpen } from "../view/screenshotViewer.js";

const THINKING_CUES = [
  "Allow me a moment, sir...",
  "Processing that for you, sir...",
  "Let me look into that, sir...",
  "Checking on that right now, sir...",
  "On it, sir...",
  "Just a moment, sir..."
];

function getRandomThinkingCue() {
  return THINKING_CUES[Math.floor(Math.random() * THINKING_CUES.length)];
}


const SESSION_KEY = "jarvis_session_id";
let sessionId = sessionStorage.getItem(SESSION_KEY);
if (!sessionId) {
  sessionId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
  sessionStorage.setItem(SESSION_KEY, sessionId);
}

// Generation counter for in-flight requests. Each submitted command bumps it;
// a reply is only acted on (spoken, shown, camera-flown) if it still belongs
// to the newest command — so answering command #2 never gets cut off by the
// late-arriving reply to command #1. Bumping it alone (cancelPendingRequests)
// orphans everything in flight.
let requestSeq = 0;

export function cancelPendingRequests() {
  requestSeq++;
}

const input = document.getElementById("chat-input");
document.getElementById("send-btn").addEventListener("click", () => handleSubmit(input.value));
input.addEventListener("keydown", (e) => { if (e.key === "Enter") handleSubmit(input.value); });

// New session: the server keys conversation history off sessionId (see
// app/history.py), so forgetting everything JARVIS remembers about this
// conversation just means dropping the id and letting the top of this file
// mint a new one on reload — no server call needed.
document.getElementById("new-session-btn").addEventListener("click", () => {
  logLine("New session requested — clearing conversation memory.", "system");
  sessionStorage.removeItem(SESSION_KEY);
  showAnswer("Starting fresh, sir.");
  setTimeout(() => location.reload(), 700);
});

export async function handleSubmit(text) {
  text = (text || "").trim();
  if (!text) return;
  input.value = "";

  if (isSpeaking()) {
    stopSpeaking();
  }

  // Only intercept as a dismiss command if there's actually a reference
  // window, viewer, Jira deck, or screenshot viewer open.
  if ((referenceWindows.hasOpenReferences() || isShowWindowOpen() || isJiraDeckOpen() || isScreenshotOpen()) && referenceWindows.isDismissCommand(text)) {
    referenceWindows.clearReferences();
    closeShowWindow();
    closeJiraDeck();
    closeScreenshot();
    logLine("Viewers dismissed.", "system");
    showAnswer("Dismissed, sir.");
    speak("Dismissed, sir.");
    return;
  }

  if (/^remember that\b/i.test(text)) {
    await handleRemember(text);
    return;
  }
  await handleChat(text);
}

// How long between spoken progress updates. Saying every change out loud
// would be intolerable — a busy turn changes activity several times a second —
// but saying nothing for two minutes is what made a rate limit look like a
// crash. Only `notable` updates are ever spoken, and never more often than
// this.
const SPOKEN_PROGRESS_GAP_MS = 25000;
let lastSpokenProgress = 0;

/** Live progress for the turn in flight: shown always, spoken rarely. */
function onChatProgress({ activity, seconds, notable, stuck }) {
  const elapsed = seconds >= 60
    ? `${Math.floor(seconds / 60)}m${String(Math.round(seconds % 60)).padStart(2, "0")}s`
    : `${Math.round(seconds)}s`;
  setStatus(`● ${activity} · ${elapsed}`);
  showAnswer(activity);
  logLine(activity, stuck ? "error" : "system");

  // Speech is the interrupting channel, so it gets the strictest filter: only
  // things that changed the situation, and only if we have been quiet a while.
  if (!notable) return;
  const now = Date.now();
  if (now - lastSpokenProgress < SPOKEN_PROGRESS_GAP_MS) return;
  lastSpokenProgress = now;
  speak(activity);
}

async function handleChat(text) {
  const seq = ++requestSeq;
  // A new turn earns one spoken update straight away; the gap only throttles
  // repeats WITHIN a turn.
  lastSpokenProgress = 0;
  const cue = getRandomThinkingCue();
  setStatus(`● thinking… (${cue})`);
  showAnswer(cue);
  speak(cue);

  logLine(`POST /chat "${text}"`, "net");
  try {
    const data = await chatRequest(text, sessionId, onChatProgress);
    if (seq !== requestSeq) {
      logLine(`(stale reply dropped: "${(data.answer || "").slice(0, 60)}…")`, "system");
      return;
    }
    setStatus(standbyStatus());
    logLine(data.answer || "(empty reply)", "reply");
    showAnswer(data.answer || "");
    speak(data.answer || "");
    if ((data.image_urls && data.image_urls.length) || (data.video_urls && data.video_urls.length)) {
      referenceWindows.showReferences(data.image_urls, data.video_urls);
    }
    // A tool failed during this turn. Show it even if the reply did not
    // mention it — a confident summary must not be the only thing the user
    // sees when something actually broke.
    if (Array.isArray(data.errors) && data.errors.length) {
      for (const err of data.errors.slice(0, 4)) logLine(err, "error");
    }

    if (data.show_url) {
      logLine(`Opening viewer: ${data.show_url}`, "system");
      openShowWindow(data.show_url);
    }
    if (data.jira_data) {
      logLine("Rendering interactive Jira Workspace deck...", "system");
      renderJiraData(data.jira_data);
    }
    if (data.screenshot_url) {
      logLine(`Displaying screenshot reference: ${data.screenshot_url}`, "system");
      openScreenshot(data.screenshot_url);
    }

    const ids = data.nodes || [];
    if (ids.length >= 4) {
      scene.flyToCluster(ids);
      closePanel();
    } else if (ids.length >= 1) {
      const node = nodeById(ids[0]);
      if (node) scene.flyToNode(node, { openPanel: true });
    }
  } catch (err) {
    if (seq !== requestSeq) return; // superseded — stay quiet
    setStatus(standbyStatus());
    logLine(`/chat request failed: ${err}`, "error");
    showAnswer("I couldn't reach the server, sir. Is server.py still running?");
  }
}

async function handleRemember(text) {
  const seq = ++requestSeq;
  const cue = getRandomThinkingCue();
  setStatus(`● thinking… (${cue})`);
  showAnswer(cue);
  speak(cue);

  logLine(`POST /remember "${text}"`, "net");
  try {
    const data = await rememberRequest(text, sessionId);
    // Note capture still lands in the graph even if superseded — the note WAS
    // filed server-side — but a stale confirmation shouldn't talk over the
    // newer command.
    if (seq !== requestSeq) {
      logLine("(stale /remember confirmation dropped)", "system");
      return;
    }
    setStatus(standbyStatus());

    if (!data.node) {
      logLine(data.confirmation || "(nothing filed)", "reply");
      showAnswer(data.confirmation || "I couldn't file that, sir.");
      speak(data.confirmation || "");
      return;
    }
    logLine(data.confirmation || "(note captured)", "reply");

    const relatedNode = data.related_id != null ? nodeById(data.related_id) : null;
    const newNode = { ...data.node };
    if (relatedNode) {
      newNode.x = relatedNode.x + (Math.random() - 0.5) * 4;
      newNode.y = relatedNode.y + (Math.random() - 0.5) * 4;
      newNode.z = relatedNode.z + (Math.random() - 0.5) * 4;
    }

    graphData.nodes.push(newNode);
    if (relatedNode) graphData.links.push({ source: relatedNode.id, target: newNode.id });
    (neighborsOf[newNode.id] ||= new Set());
    if (relatedNode) {
      neighborsOf[newNode.id].add(relatedNode.id);
      (neighborsOf[relatedNode.id] ||= new Set()).add(newNode.id);
    }

    scene.refreshGraphData();
    setNoteCount(data.notes_count);

    showAnswer(data.confirmation || "");
    speak(data.confirmation || "");

    setTimeout(() => scene.flyToNode(newNode, { openPanel: true }), 400);
    setTimeout(scene.clearHighlight, 3200);
  } catch (err) {
    if (seq !== requestSeq) return; // superseded — stay quiet
    setStatus(standbyStatus());
    logLine(`/remember request failed: ${err}`, "error");
    showAnswer("I couldn't file that, sir — is server.py still running?");
  }
}
