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

/**
 * @param onProgress Called with {activity, seconds, notable, stuck} each time
 *   the server reports something new about the running turn. `notable` is true
 *   only for the handful of updates worth interrupting the user over.
 */
export async function chatRequest(message, sessionId, onProgress, images) {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message, session_id: sessionId, async: true,
      // Camera frames from this device, as data: URLs. Omitted entirely when
      // the eyes are shut, so an ordinary turn sends exactly what it did.
      ...(images && images.length ? { images } : {}),
    }),
  });
  const started = await res.json();
  if (!started.job_id) return started; // server answered synchronously

  const deadline = Date.now() + CHAT_POLL_DEADLINE_MS;
  let lastActivity = null;
  let lastNotable = 0;
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

    // Only report genuine CHANGES. The poll fires every 1.5s; re-announcing
    // the same line each time would turn a status into a stutter.
    if (onProgress && data.activity && data.activity !== lastActivity) {
      lastActivity = data.activity;
      const notable = (data.notable_seq || 0) > lastNotable;
      lastNotable = data.notable_seq || 0;
      onProgress({ activity: data.activity, seconds: data.seconds,
                   notable, stuck: !!data.stuck });
    }
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
