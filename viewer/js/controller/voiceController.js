// js/controller/voiceController.js — mic input (Controller layer):
// SpeechRecognition setup, wake-word gating, conversation-mode state
// machine, and utterance accumulation/debounce so a long sentence isn't cut
// off by Chrome's internal speech-segment finalization (continuous
// recognition force-finalizes a "final" result on its own timing — often
// well under 10s into a long sentence — not necessarily on a real pause).
//
// Doesn't call chatController.handleSubmit directly (that would make this
// module depend on chatController, which depends back on this module's
// standbyStatus() — circular). Instead it calls the onCommand callback
// injected via initVoiceController(); main.js wires that to
// chatController.handleSubmit at boot.
import { speak } from "./speechController.js";
import { showAnswer } from "../view/toast.js";
import { setStatus } from "../view/statusLine.js";

const micBtn = document.getElementById("mic-btn");
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

let WAKE_RE = /\bjarvis\b[,:]?\s*/i;
let AWAIT_COMMAND_MS = 6000;
// How long to wait after the user stops producing new speech events before
// treating an utterance as complete. This — not Chrome's own internal segment
// finalization — is what decides "is he done talking": acting on the first
// final chunk is what used to cut people off mid-sentence. Every final AND
// interim chunk just keeps extending this timer instead of being acted on
// directly, so a long sentence spanning several internal segments (or even a
// background recognizer restart — see onend below) reads as one utterance.
let SILENCE_COMMIT_MS = 1300;
let CONVO_CLOSINGS = ["Very good, sir. I'll be here when you need me."];

// Loosely-matched, unambiguous session-enders — safe to match anywhere at the
// start of the utterance since they essentially never open a genuine follow-up
// request in the same breath.
const CONVO_END_STRONG_RE = /^(?:ok(?:ay)?[,.]?\s*)?(bye|goodbye|good\s*bye|see\s+you|we'?re\s+done|i'?m\s+done|(you'?re\s+|you\s+are\s+|you\s+)?good\s+to\s+go|no(?:thing)?\s+else(?:\s+for\s+now)?|that'?s?\s+(?:all|it|everything)(?:\s+for\s+now)?|that(?:'?ll|\s+will)\s+be\s+all|stop\s+listening|go\s+to\s+(?:sleep|standby))\b/i;
// "Thanks"/"thank you" are closing signals but genuinely ambiguous mid-conversation
// ("thanks, and also check the weather") — only treat them as a goodbye when they
// ARE the utterance (plus minor trailing pleasantries or his name), not just its
// opening words.
const CONVO_END_THANKS_RE = /^thanks?(?:\s+(?:very\s+much|so\s+much|a\s+lot))?(?:\s+jarvis)?[.!]?$|^thank\s+you(?:\s+(?:very\s+much|so\s+much))?(?:\s+jarvis)?[.!]?$|^thanks?\s+for\s+(?:your\s+|the\s+)?help(?:ing)?(?:\s+me)?(?:\s+jarvis)?[.!]?$/i;
let extraClosingRe = null; // built from config.conversation.extra_closing_phrases

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function isConversationClosing(text) {
  const t = text.trim();
  if (CONVO_END_STRONG_RE.test(t) || CONVO_END_THANKS_RE.test(t)) return true;
  return extraClosingRe ? extraClosingRe.test(t) : false;
}

let recognizer = null;
let listeningEnabled = false;   // always-on mode, toggled by the mic button
let awaitingCommand = false;    // true right after a bare "Jarvis" with no command attached
let awaitTimer = null;
let utteranceBuffer = "";       // accumulated finalized chunks of the in-progress utterance
let interimChunk = "";          // most recent not-yet-final chunk
let silenceCommitTimer = null;
let conversationActive = false; // true once a real exchange has happened — while true,
                                 // no "Jarvis" prefix is needed for follow-ups, until a
                                 // closing phrase (see isConversationClosing) ends it.
let onCommand = () => {};

export function standbyStatus() {
  if (!listeningEnabled) return "";
  return conversationActive ? "● in conversation — say “bye” to end" : "◌ standby — say “Jarvis” to talk";
}

// Pause the mic while JARVIS is talking so he doesn't hear (and react to)
// his own voice. Injected into speechController via setMicHooks().
export function pauseMic() {
  const wasListening = listeningEnabled && recognizer;
  if (wasListening) { try { recognizer.stop(); } catch (e) { /* not running */ } }
  return wasListening;
}

export function resumeMic(wasListening) {
  if (wasListening && listeningEnabled) { try { recognizer.start(); } catch (e) { /* already running */ } }
}

function armAwaitingCommand() {
  awaitingCommand = true;
  clearTimeout(awaitTimer);
  awaitTimer = setTimeout(() => { awaitingCommand = false; setStatus(standbyStatus()); }, AWAIT_COMMAND_MS);
}

function endConversation() {
  conversationActive = false;
  const bye = CONVO_CLOSINGS[Math.floor(Math.random() * CONVO_CLOSINGS.length)];
  showAnswer(bye);
  speak(bye);
  setStatus(standbyStatus());
}

function processHeardText(text) {
  if (!text) return;

  // Once a conversation is underway, no wake word is needed for follow-ups —
  // every utterance is either the closing phrase or a new command, until told
  // to stop. This is the whole point of conversation mode: keep interacting
  // turn after turn without repeating "Jarvis" each time.
  if (conversationActive) {
    if (isConversationClosing(text)) {
      endConversation();
      return;
    }
    // Strip a wake-word prefix if they say it out of habit, but don't require it.
    const inline = text.match(WAKE_RE);
    const command = inline ? text.slice(inline.index + inline[0].length).trim() : text;
    if (command) onCommand(command);
    return;
  }

  if (awaitingCommand) {
    clearTimeout(awaitTimer);
    awaitingCommand = false;
    conversationActive = true;
    onCommand(text);
    return;
  }

  const match = text.match(WAKE_RE);
  if (!match) return; // not addressed to JARVIS — ignored, nothing sent anywhere

  const command = text.slice(match.index + match[0].length).trim();
  if (command) {
    conversationActive = true;
    onCommand(command);
  } else {
    speak("Yes, sir?");
    showAnswer("Yes, sir?");
    armAwaitingCommand();
    setStatus("● listening for your command…");
  }
}

function commitUtterance() {
  const text = (utteranceBuffer + " " + interimChunk).trim();
  utteranceBuffer = "";
  interimChunk = "";
  processHeardText(text);
}

export function hasRecognizer() {
  return !!recognizer;
}

// Sets listeningEnabled without starting the recognizer — used by the
// wake-btn boot flow, which lets speak()'s own completion (via resumeMic)
// start the recognizer once the greeting finishes, rather than racing it.
export function enableListening() {
  listeningEnabled = true;
}

export function initVoiceController(config, commandCallback) {
  onCommand = commandCallback;

  const wakePattern = config?.wake_word?.pattern || "jarvis";
  WAKE_RE = new RegExp(`\\b${escapeRegExp(wakePattern)}\\b[,:]?\\s*`, "i");
  AWAIT_COMMAND_MS = config?.wake_word?.await_command_ms ?? 6000;
  SILENCE_COMMIT_MS = config?.wake_word?.silence_commit_ms ?? 1300;
  if (config?.conversation?.closing_lines?.length) {
    CONVO_CLOSINGS = config.conversation.closing_lines;
  }
  const extra = config?.conversation?.extra_closing_phrases || [];
  if (extra.length) {
    const alternation = extra.map(p => escapeRegExp(String(p).trim())).filter(Boolean).join("|");
    if (alternation) extraClosingRe = new RegExp(`^(?:${alternation})\\b`, "i");
  }

  if (!SpeechRecognition) {
    micBtn.disabled = true;
    micBtn.title = "Voice input needs Chrome or Edge";
    return;
  }

  recognizer = new SpeechRecognition();
  recognizer.lang = "en-US";
  recognizer.continuous = true;
  recognizer.interimResults = true;
  recognizer.maxAlternatives = 1;

  recognizer.onstart = () => {
    micBtn.classList.add("recording");
    setStatus(awaitingCommand ? "● listening for your command…" : standbyStatus());
  };
  recognizer.onerror = (e) => {
    // "no-speech" fires constantly in continuous mode while idle — not a real error.
    if (e.error !== "no-speech") console.warn("[jarvis] speech recognition error:", e.error);
  };
  recognizer.onend = () => {
    micBtn.classList.remove("recording");
    if (listeningEnabled) {
      // Chrome periodically stops continuous recognition on its own — restart to
      // stay always-on. utteranceBuffer/interimChunk and the silence-commit timer
      // live outside the recognizer instance, so an in-progress utterance survives
      // this restart seamlessly — only the brief reconnect gap is lost, not the rest
      // of the sentence.
      try { recognizer.start(); } catch (e) { /* already running */ }
    } else {
      setStatus("");
    }
  };
  recognizer.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const result = e.results[i];
      if (result.isFinal) {
        utteranceBuffer = (utteranceBuffer + " " + result[0].transcript).trim();
        interimChunk = "";
      } else {
        interimChunk = result[0].transcript;
      }
    }
    // Any speech activity — final or still-interim — pushes back the commit,
    // rather than acting on the first final chunk immediately.
    clearTimeout(silenceCommitTimer);
    silenceCommitTimer = setTimeout(commitUtterance, SILENCE_COMMIT_MS);
    // Speech is actively arriving, so cancel the separate "gave up waiting for
    // a command after a bare 'Jarvis'" timeout — commitUtterance now owns
    // deciding when the response is actually finished.
    if (awaitingCommand) clearTimeout(awaitTimer);
  };

  micBtn.addEventListener("click", () => {
    listeningEnabled = !listeningEnabled;
    if (listeningEnabled) {
      try { recognizer.start(); } catch (e) { /* already running */ }
    } else {
      awaitingCommand = false;
      conversationActive = false;
      clearTimeout(awaitTimer);
      clearTimeout(silenceCommitTimer);
      utteranceBuffer = "";
      interimChunk = "";
      try { recognizer.stop(); } catch (e) { /* not running */ }
      setStatus("");
    }
  });
}
