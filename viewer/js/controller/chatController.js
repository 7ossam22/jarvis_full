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
import { speak } from "./speechController.js";
import { standbyStatus } from "./voiceController.js";

const SESSION_KEY = "jarvis_session_id";
let sessionId = sessionStorage.getItem(SESSION_KEY);
if (!sessionId) {
  sessionId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
  sessionStorage.setItem(SESSION_KEY, sessionId);
}

const input = document.getElementById("chat-input");
document.getElementById("send-btn").addEventListener("click", () => handleSubmit(input.value));
input.addEventListener("keydown", (e) => { if (e.key === "Enter") handleSubmit(input.value); });

export async function handleSubmit(text) {
  text = (text || "").trim();
  if (!text) return;
  input.value = "";

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
  setStatus("● thinking…");
  try {
    const data = await chatRequest(text, sessionId);
    setStatus(standbyStatus());
    showAnswer(data.answer || "");
    speak(data.answer || "");
    if (data.image_urls && data.image_urls.length) referenceWindows.showReferences(data.image_urls);
    // No image(s) on this reply: leave whatever reference windows are already
    // showing exactly as-is — they only change on a new batch or a dismiss.

    const ids = data.nodes || [];
    if (ids.length >= 4) {
      scene.flyToCluster(ids);
      closePanel();
    } else if (ids.length >= 1) {
      const node = nodeById(ids[0]);
      if (node) scene.flyToNode(node, { openPanel: true });
    }
    // small talk (ids.length === 0): no camera movement, per the personality spec.
  } catch (err) {
    setStatus(standbyStatus());
    showAnswer("I couldn't reach the server, sir. Is server.py still running?");
  }
}

async function handleRemember(text) {
  setStatus("● thinking…");
  try {
    const data = await rememberRequest(text, sessionId);
    setStatus(standbyStatus());
    if (!data.node) {
      showAnswer(data.confirmation || "I couldn't file that, sir.");
      speak(data.confirmation || "");
      return;
    }

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
    showAnswer("I couldn't file that, sir — is server.py still running?");
  }
}
