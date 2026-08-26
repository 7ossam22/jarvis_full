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
  // window open — otherwise a stray "close that" falls through to Claude
  // like any other question, rather than silently swallowing it.
  if (referenceWindows.hasOpenReferences() && referenceWindows.isDismissCommand(text)) {
    referenceWindows.clearReferences();
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

async function handleChat(text) {
  const cue = getRandomThinkingCue();
  setStatus(`● thinking… (${cue})`);
  showAnswer(cue);
  speak(cue);

  logLine(`POST /chat "${text}"`, "net");
  try {
    const data = await chatRequest(text, sessionId);
    setStatus(standbyStatus());
    logLine(data.answer || "(empty reply)", "reply");
    showAnswer(data.answer || "");
    speak(data.answer || "");
    if ((data.image_urls && data.image_urls.length) || (data.video_urls && data.video_urls.length)) {
      referenceWindows.showReferences(data.image_urls, data.video_urls);
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
    setStatus(standbyStatus());
    logLine(`/chat request failed: ${err}`, "error");
    showAnswer("I couldn't reach the server, sir. Is server.py still running?");
  }
}

async function handleRemember(text) {
  const cue = getRandomThinkingCue();
  setStatus(`● thinking… (${cue})`);
  showAnswer(cue);
  speak(cue);

  logLine(`POST /remember "${text}"`, "net");
  try {
    const data = await rememberRequest(text, sessionId);
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
    setStatus(standbyStatus());
    logLine(`/remember request failed: ${err}`, "error");
    showAnswer("I couldn't file that, sir — is server.py still running?");
  }
}
