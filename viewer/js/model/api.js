// js/model/api.js — thin fetch wrappers for the server API (Model layer:
// server I/O). No UI/DOM logic here — callers (controllers) decide what to
// do with the result.

export async function chatRequest(message, sessionId) {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  return res.json();
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
