# JARVIS — a talking AI second brain

An interactive 3D "Neural Core" built from a folder of markdown notes, with a
voice-enabled AI ([Claude](https://claude.com)) that answers from those notes
(falling back to a live web search when they don't cover it), speaks with a
real voice, shows image lookups in floating reference windows, and lets you
grow the graph live by voice ("remember that…"). Originally built by
following the [Build Your Own JARVIS](https://skool.com/aiworkshop) prompt
pack, then extended well past it.

No frameworks, no npm, no build step. Python 3 standard library on the
backend (organized MVC-style, see below), plain HTML/CSS + native ES modules
+ [3d-force-graph](https://github.com/vasturiano/3d-force-graph) (from a CDN)
on the frontend.

## What's here

- **The Neural Core** — `build.py` scans `notes/*.md`, links notes that
  mention each other's titles or share `[[wikilinks]]`, and writes
  `viewer/graph-data.js`. `viewer/index.html` renders it as a translucent,
  procedurally-deformed brain shell with each note as a contained "neuron" —
  click one to fly the camera to it and read its excerpt in the side panel.
  The whole brain visibly pulses while JARVIS is speaking.
- **The Brain** — `POST /chat` scores every note against your question by
  keyword overlap (title matches weigh extra), hands the top matches to
  Claude with a "judge for yourself if these are actually relevant" system
  prompt, and falls through to a live web search when nothing fits or the
  question is about the outside world (current events, prices, "what does X
  look like").
- **The Voice** — real speech via a server-side [ElevenLabs](https://elevenlabs.io)
  proxy (the API key never reaches the browser), with an automatic fallback
  to the browser's built-in `speechSynthesis` if no key is configured or the
  call fails.
- **Always-listening conversation mode** — say "Jarvis" once to start a
  conversation; after that, no wake word is needed for follow-ups until you
  say something like "bye", "thanks for helping", or "that'll be all".
  Long sentences aren't cut off mid-thought — speech is accumulated and only
  committed after real silence, not on Chrome's own internal segment
  timing.
- **Image lookups** — when a web search turns up a relevant image (or you
  ask for a gallery of several), JARVIS shows it in a floating reference
  window at a random spot on screen. It stays there until you dismiss it
  ("close that", "dismiss those") or ask for a new lookup.
- **The Personality** — a dry, witty British butler, fully configurable (see
  `config.json` below) — name, address term, tone, and more.
- **Total Recall** — say or type "remember that…" and JARVIS writes a real
  markdown note into `notes/captures/`, births a new node at its most
  related neighbor, flies to it, and confirms out loud with one witty line.

This repo ships with 25 sample notes (a small coffee-roasting business, Nova
Roasters) so it works out of the box — swap in your own notes folder any time.

## Setup (5 minutes)

**You'll need:** Python 3, Google Chrome (the mic and voice need it — Safari
won't cut it), and optionally an
[Anthropic API key](https://console.anthropic.com) and/or an
[ElevenLabs API key](https://elevenlabs.io).

1. **Give it a brain and a voice.**

   ```bash
   cp config.example.json config.json
   ```

   Open `config.json` and fill in what you have:

   ```json
   {
     "model": { "provider_api_key": "sk-ant-...", "model_id": "claude-sonnet-5" },
     "voice": { "elevenlabs_api_key": "sk_...", "elevenlabs_voice_id": "..." }
   }
   ```

   You only need to include the keys you're actually overriding —
   `config.json` is deep-merged on top of `config.example.json`'s defaults,
   so a minimal file with just your real secrets is enough (see
   **Configuring JARVIS** below for the full schema).

   ⚠️ **Never paste your API key into a chat window** — type it directly into
   this file yourself. `config.json` lives at the project root (not inside
   `viewer/`), is gitignored, and the server never serves it or exposes it
   through `/config` (the endpoint the frontend uses to read *non-secret*
   settings) — so it's never reachable from the browser.

   **No Anthropic key?** Skip it. If the
   [`claude` CLI](https://claude.com/claude-code) is installed and logged in,
   the server automatically falls back to `claude -p` — slower, but free on
   your Claude Code subscription. With neither configured, JARVIS still runs
   and answers honestly that his brain isn't wired up yet.

   **No ElevenLabs key?** Skip it too — JARVIS falls back to the browser's
   built-in voice automatically.

2. **Point it at your notes** (optional). By default JARVIS reads the sample
   `notes/` folder in this repo. To use your own vault instead:

   ```bash
   python3 build.py /path/to/your/notes
   ```

   (The server also rebuilds the graph from `notes/` automatically every
   time it starts and every time you ask a question, so captured notes
   always stay in sync — point the live server permanently at your own
   vault by changing `NOTES_DIR` in `app/http_server.py`.)

3. **Launch it.**

   ```bash
   python3 server.py
   ```

   Open **http://127.0.0.1:4700** in Chrome, click **Wake JARVIS** (browsers
   block audio until you interact with the page once), and fly around.

## Configuring JARVIS

Almost every behavior tunable lives in `config.json` — see
`config.example.json` for the full schema with defaults:

| Section | Controls |
|---|---|
| `server` | Port and bind address (`0.0.0.0` reaches your LAN, `127.0.0.1` is local-only) |
| `model` | Three interchangeable LLM backends behind one interface (`app/providers/llm.py`) — Anthropic Claude (`provider_api_key`, `model_id`; falls back to the local `claude` CLI with no key), Google Gemini (`gemini_api_key`, `gemini_model_id`), and a **local model over the LAN via LM Studio** (`lmstudio_base_url`, e.g. `http://192.168.1.50:1234/v1`; optional `lmstudio_model_id`, `lmstudio_api_key`, `lmstudio_use_tools`). Set `"provider"` to `"gemini"`, `"anthropic"`, or `"lmstudio"` to pick which one is tried first; whichever is configured but not chosen is kept as automatic failover. Leave `provider` unset and Anthropic goes first, for backward compatibility. The local model runs the same connector tool loop (Gmail/Discord/Jira/browser/system) but has no web search. |
| `persona` | Name, address term ("sir"), tone description, and a free-text `system_prompt_extra` for tweaks that don't need a code change |
| `voice` | Three interchangeable TTS backends behind one interface (`app/providers/tts.py`) — ElevenLabs, Fish Audio, and local Kokoro. Set `"tts_provider"` to pick which one is tried first, same failover behavior as `model.provider` above. |
| `wake_word` | The wake word itself, how long to wait for a command after a bare "Jarvis", and how long to wait for silence before treating a sentence as finished |
| `conversation` | Extra closing phrases to end conversation mode, and the spoken sign-off lines |
| `retrieval` | How many notes to retrieve per question, how many turns of history to keep |
| `images` | Max images per gallery lookup |
| `brain` | Radius and colors of the 3D brain visualization |

Edit `config.json` and reload the page (and restart the server for `server.*`
changes) — no code changes needed for anything in this list.

## Try it

- Click any node to see its excerpt and fly the camera to it.
- Ask by typing or by clicking 🎙 and speaking:
  - *"What supplier do we use for the seasonal blend?"*
  - *"Who's on the espresso machine training track?"*
  - *"What does the Eiffel Tower look like?"* — watch a reference window appear.
  - *"Show me 3 images of the Golden Gate Bridge"* — a small gallery instead of one.
- Say *"remember that prompt packs make excellent free gifts"* and watch a new
  node get born.
- Say *"tell me a joke"* — JARVIS will banter without touching the camera.
- After your first exchange, keep talking without saying "Jarvis" again —
  say *"bye"* or *"thanks for helping"* when you're done.

## Project layout

MVC-organized: **Model** (settings, notes/graph data, external services),
**View** (rendering — the 3D scene, panels, toasts), **Controller** (request
handling and chat/voice orchestration).

```
server.py                    thin entry point — `from app.http_server import main`
build.py                     thin CLI wrapper around app/graph.py, for standalone runs
config.example.json          full config schema + defaults — copy to config.json
config.json                  your overrides + real secrets (gitignored)

app/                         backend (Model + Controller)
  config.py                    Config: loads/merges config.json, public_dict() for the browser
  graph.py                     scans notes/, writes viewer/graph-data.js
  retrieval.py                 keyword-overlap note search
  history.py                   in-memory per-session conversation history
  persona.py                   assembles the system prompt from config
  images.py                    parses the model's IMAGE: lines into gallery URLs
  providers/
    anthropic_provider.py        Claude API call + `claude -p` CLI fallback
    gemini_provider.py           Google Gemini API call
    lmstudio_provider.py         local model on the LAN via LM Studio (OpenAI-compatible)
    elevenlabs_provider.py       ElevenLabs text-to-speech
  controllers.py                request handling logic (no HTTP specifics)
  http_server.py                HTTP routing, static file serving, GET /config

viewer/                      frontend (View + Controller)
  index.html                   markup only
  graph-data.js                 generated by build.py — not committed
  css/main.css                  all styling
  js/
    main.js                      boot sequence — wires everything together
    model/                       config.js, api.js, graphData.js
    view/                        scene.js (3D brain), panel.js, toast.js,
                                  referenceWindows.js, statusLine.js, hud.js
    controller/                  voiceController.js, speechController.js,
                                  chatController.js

notes/                        sample markdown notes (25 notes, Nova Roasters coffee co.)
notes/captures/                notes JARVIS writes for you via "remember that…" (gitignored)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Mic button does nothing | Chrome → lock icon in the address bar → allow Microphone. Must be Chrome or Edge. |
| No sound | Click **Wake JARVIS** once — browsers block audio before the first interaction. |
| Page looks stale after a change | Hard reload: `Cmd+Shift+R` / `Ctrl+Shift+R`. |
| "I appear to be without a working brain" | No LLM backend is usable: no `model.provider_api_key` (Anthropic), `model.gemini_api_key` (Gemini), or `model.lmstudio_base_url` (LM Studio) in `config.json`, and the `claude` CLI isn't installed/logged in either. |
| JARVIS uses the browser voice instead of a real one | None of the three TTS backends (ElevenLabs/Fish Audio/Kokoro) are configured, or all of them failed — check the server's console output. |
| Answers are generic / off-topic | Your notes folder is thin on that topic, or you pointed `build.py` at the wrong path — re-run `python3 build.py /full/path/to/notes`. |
| Port 4700 already in use | Another `server.py` is still running — stop it, or edit `server.port` in `config.json`. |
| Mic cuts off while you're still talking | Should no longer happen — see `wake_word.silence_commit_ms` in `config.json` if you want it more/less patient. |

## Security notes

- `config.json` (your real API keys) is gitignored and never served over
  HTTP — only files under `viewer/` are reachable from the browser, and
  `GET /config` returns a curated non-secret subset only (persona, wake
  word, timers, gallery cap, brain visuals) — never `provider_api_key` or
  `elevenlabs_api_key`.
- The server only serves files inside `viewer/`; path traversal (`..`,
  absolute paths) is rejected.
- ElevenLabs TTS is proxied server-side specifically so the API key never
  reaches the browser (or anyone else on the LAN, if `server.bind` is set to
  `0.0.0.0`).
- Conversation history is kept in memory per session and is lost on server
  restart — nothing is persisted beyond the notes themselves.

---

*Prompt pack: "Build Your Own JARVIS" by Zubair Trabzada — AI Workshop
([skool.com/aiworkshop](https://skool.com/aiworkshop)). This build follows all
six original prompts (Galaxy, Brain, Voice, Magic, Personality, Total Recall)
as its starting spec, then extends past it with a real voice, web search,
image galleries, hands-free conversation mode, and an MVC restructure.*
