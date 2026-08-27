// js/model/config.js — fetches the server's public config once at boot
// (Model layer). Falls back to built-in defaults if the fetch fails, so a
// transient network hiccup doesn't hard-crash the boot sequence.

const FALLBACK_CONFIG = {
  persona: { name: "JARVIS", address_term: "sir" },
  wake_word: { pattern: "jarvis", await_command_ms: 6000, silence_commit_ms: 1300 },
  conversation: {
    extra_closing_phrases: [],
    closing_lines: ["Very good, sir. I'll be here when you need me."],
  },
  images: { max_gallery: 6 },
  brain: { radius: 140, shell_color: "#e2e8f0", wire_color: "#94a3b8" },
};

export async function fetchConfig() {
  try {
    const res = await fetch("/config");
    if (!res.ok) throw new Error(`config endpoint returned ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("[jarvis] /config fetch failed, using built-in fallback:", err);
    return FALLBACK_CONFIG;
  }
}
