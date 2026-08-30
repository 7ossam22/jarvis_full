// js/model/api.js — thin fetch wrappers for the server API (Model layer:
// server I/O). No UI/DOM logic here — callers (controllers) decide what to
// do with the result.

// Long tool-using runs (filling big forms, multi-step browser work) can far
// outlive the browser's built-in request timeout, so the server runs the chat
// as a background job and we poll for the answer instead of holding one
// request open the whole time.
const CHAT_POLL_INTERVAL_MS = 1500;
// Matches the server's _CHAT_JOB_MAX_AGE — a full "complete visit" run over
// many long forms can legitimately take this long, and giving up client-side
// before the server does left the workflow finishing invisibly.
const CHAT_POLL_DEADLINE_MS = 60 * 60 * 1000;

export async function chatRequest(message, sessionId) {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, async: true }),
  });
  const started = await res.json();
  if (!started.job_id) return started; // server answered synchronously

  const deadline = Date.now() + CHAT_POLL_DEADLINE_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, CHAT_POLL_INTERVAL_MS));
    let data;
    try {
      const poll = await fetch(`/chat/result?job_id=${started.job_id}`);
      data = await poll.json();
    } catch {
      continue; // transient network hiccup — keep polling
    }
    if (data.status === "done") return data.result;
    if (data.status === "unknown") throw new Error("chat job vanished on the server");
  }
  throw new Error("chat job timed out");
}

export async function rememberRequest(text, sessionId) {
  const res = await fetch("/remember", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, session_id: sessionId }),
  });
  return res.json();
}

export async function speakRequest(text) {
  const res = await fetch("/speak", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`speak endpoint returned ${res.status}`);
  return res.blob();
}
