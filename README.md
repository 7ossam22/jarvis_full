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
- **The Voice** — real speech from [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
  running locally on this machine (`tools/setup_kokoro.sh`): no API key, no
  per-character billing, no audio leaving the box. Cloud backends
  ([ElevenLabs](https://elevenlabs.io), [Fish Audio](https://fish.audio)) are
  still there behind the same interface and are still proxied server-side so
  their keys never reach the browser. All of them fall back to the browser's
  built-in `speechSynthesis` if nothing is configured or the call fails.
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

   **Want a real voice without paying for one?** Install Kokoro locally
   instead (below).

2. **Give it a local voice** (recommended). Kokoro-82M runs on this machine,
   costs nothing per word, and needs no API key:

   ```bash
   tools/setup_kokoro.sh
   ```

   That builds `.venv-kokoro` (CPU torch — the model is small, and this
   leaves the GPU free), downloads Kokoro-82M, and installs a
   `systemd --user` service so the voice box comes back on login. Then point
   `config.json` at it:

   ```json
   {
     "voice": {
       "tts_provider": "kokoro",
       "tts_failover": false,
       "kokoro_base_url": "http://127.0.0.1:8880",
       "kokoro_voice": "bf_emma"
     }
   }
   ```

   `tts_failover: false` keeps JARVIS on the local voice only, so a hiccup
   never quietly bills a cloud provider instead.

   The voice box (`tools/kokoro_server.py`) serves the same
   OpenAI-compatible `POST /v1/audio/speech` that
   [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) does, so you
   can swap in that project — or a Docker container, or a box elsewhere on
   the LAN — by changing `kokoro_base_url` and nothing else. It binds to
   `127.0.0.1` only; the JARVIS server proxies it, exactly as it does the
   cloud backends.

   Handy commands:

   ```bash
   systemctl --user status kokoro-tts       # is it up?
   systemctl --user restart kokoro-tts      # after changing the service file
   journalctl --user -u kokoro-tts -n 50    # why isn't it up?
   curl http://127.0.0.1:8880/v1/audio/voices   # every voice pack available
   ```

   Non-English voices need their own G2P engine on top of the base install:
   the Japanese packs (`jf_alpha`, `jf_gongitsune`, `jf_nezumi`,
   `jf_tebukuro`, `jm_kumo`) want `tools/setup_kokoro.sh --japanese`, without
   which they fail with `No module named 'pyopenjtalk'` or `Failed
   initializing MeCab`. Chinese (`zf_`/`zm_`) needs `misaki[zh]` the same way.

   British female packs are `bf_emma`, `bf_isabella`, `bf_alice`, `bf_lily`;
   British male are `bm_george`, `bm_lewis`, `bm_fable`, `bm_daniel`. The
   `af_`/`am_` packs are American — `af_heart` and `af_bella` are the
   best-sounding female voices in the model, at the cost of the accent. A
   voice pack's first letter is its language and the second its gender
   (`bf_` = British female), so `kokoro_voice` alone switches accent,
   language, and gender.

3. **Point it at your notes** (optional). By default JARVIS reads the sample
   `notes/` folder in this repo. To use your own vault instead:

   ```bash
   python3 build.py /path/to/your/notes
   ```

   (The server also rebuilds the graph from `notes/` automatically every
   time it starts and every time you ask a question, so captured notes
   always stay in sync — point the live server permanently at your own
   vault by changing `NOTES_DIR` in `app/http_server.py`.)

4. **Launch it.**

   ```bash
   python3 server.py
   ```

   Open **http://127.0.0.1:4700** in Chrome, click **Wake JARVIS** (browsers
   block audio until you interact with the page once), and fly around.

## Running this on another machine

Everything secret comes from `config.json` (gitignored) or environment
variables — nothing is baked into the source. But a few **absolute paths and
host-specific values are still hardcoded**, so a fresh clone needs the steps
below before the browser/Novatek automation will work.

### 1. Prerequisites

- **Python 3.12+** — standard library only for the server itself, no pip install.
- **Google Chrome** — required for the mic and voice; Safari/Firefox won't work.
- **Playwright + Chromium**, only if you want the browser/Flutter automation:

  ```bash
  ./tools/setup_browser.sh
  ```

  This creates `.venv-browser/` and downloads Chromium into `~/.cache/ms-playwright`.
  No sudo. The server starts `tools/browser_daemon.py` from that venv on demand
  (localhost:4701) — you never launch it yourself.

### 2. Create `config.json`

```bash
cp config.example.json config.json
```

`config.json` is deep-merged over `config.example.json`, so include only what
you actually override. Any value still reading `YOUR-…` or `PUT-YOUR-…` is
treated as *unset*, never as a real credential.

```json
{
  "model":   { "provider": "anthropic", "provider_api_key": "sk-ant-..." },
  "voice":   { "tts_provider": "elevenlabs", "elevenlabs_api_keys": ["sk_..."] },
  "gmail":   { "access_token": "ya29...." },
  "discord": { "bot_token": "..." },
  "jira":    { "domain": "https://you.atlassian.net", "email": "you@example.com", "api_token": "ATATT..." },
  "novatek": { "username": "...", "password": "..." }
}
```

Every connector is optional and degrades gracefully — an unconfigured one
reports that it needs setting up rather than failing the whole turn.

### 3. Or supply secrets as environment variables

Useful for CI, containers, or keeping credentials out of files entirely.
**`config.json` wins where both are set.** These are read by the connectors
and by `Config.novatek_credentials()`:

| Variable | Replaces |
|---|---|
| `GMAIL_ACCESS_TOKEN` | `gmail.access_token` |
| `DISCORD_BOT_TOKEN` | `discord.bot_token` |
| `JIRA_DOMAIN`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | the `jira` section |
| `NOVATEK_USERNAME`, `NOVATEK_PASSWORD` | the `novatek` section |

The Anthropic / Gemini / ElevenLabs keys have **no** environment-variable
path — those must go in `config.json`.

The browser daemon inherits the server's environment, so exporting
`NOVATEK_*` before `python3 server.py` reaches the form autopilot too.

### 4. Fix the hardcoded host paths

These are the only values that genuinely will not work on another machine.
Skip any whose feature you don't use.

| File and line | Change it to |
|---|---|
| `app/persona.py:59` and `:66` | The absolute path of the PDF used to answer File Upload questions |
| `tools/browser_daemon.py:1749` | Same PDF path — the autopilot's fallback default |
| `tools/browser_daemon.py:84-87` | Your Flutter SDK paths, or delete the list if you never run `flutter_run_test` |
| `app/persona.py:40` | The example Chrome profile names (`Hossam`, `Doxx`, …) — replace with yours, or run `browser_list_profiles` to discover them |
| `app/http_server.py` `NOTES_DIR` | Point the live server at your own notes vault instead of `notes/` |

The Novatek portal URLs live in `novatek.sites` in `config.json` (`nec` and `hcc` by
default) with `novatek.default_site` choosing which a bare "open Novatek" uses. That map is
the single source of truth: the `novatek_open` tool resolves names from it, the persona rules
list it, and the browser display policy exempts every one of its hostnames automatically.
Per-site `username`/`password` override the shared `novatek.username`/`password`. The old
single hardcoded URL (`app/persona.py`)
is deployment-specific too — change it if you target a different instance, and
delete the whole Novatek rules block from `RULES_TEMPLATE` if you don't use it
at all.

### 5. Launch and verify

```bash
python3 server.py
```

Open **http://127.0.0.1:4700** in Chrome and click **Wake JARVIS**.

To confirm what the machine actually resolved:

```bash
python3 -c "import sys; sys.path.insert(0,'.'); from app.config import Config; \
c=Config.load(); print('LLM key:', c.has_real_api_key()); \
print('Novatek:', c.novatek_credentials()[0] or 'not configured')"
```

> **Before exposing it:** `server.bind` defaults to `0.0.0.0`, which reaches
> your whole LAN, and the server has **no authentication on any endpoint**.
> `POST /chat` can reach your configured Gmail, Discord, Jira and Novatek
> credentials, plus `system_run_command`. On any shared or untrusted network,
> set `"server": { "bind": "127.0.0.1" }` in `config.json`.

## Configuring JARVIS

Almost every behavior tunable lives in `config.json` — see
`config.example.json` for the full schema with defaults:

| Section | Controls |
|---|---|
| `server` | Port and bind address (`0.0.0.0` reaches your LAN, `127.0.0.1` is local-only) |
| `model` | Three interchangeable LLM backends behind one interface (`app/providers/llm.py`) — Anthropic Claude (`provider_api_key`, `model_id`), Google Gemini (`gemini_api_key`, `gemini_model_id`), and a **local model over the LAN via LM Studio** (`lmstudio_base_url`, e.g. `http://192.168.1.50:1234/v1`; optional `lmstudio_model_id`, `lmstudio_api_key`, `lmstudio_use_tools`), plus local CLI backends (**Antigravity `agy`** and Claude `claude`). Both Claude and Gemini fall back to local CLI execution if no API key is set or the API fails/exhausts quota. Set `"cli_fallback"` in `config.json` to `"agy"` (Antigravity), `"claude"`, `"auto"` (default, with automatic failover between them), or `"none"` (to disable). You can also set `"provider"` directly to `"agy"` or `"claude"` to run without any API key. The local model runs the same connector tool loop (Gmail/Discord/Jira/browser/system) but has no web search. `"assist_provider"` overrides that choice **for the Novatek form autopilot's stuck-escalation questions only** (`POST /assist/form-question`) — set it to `"lmstudio"` when your main provider is a metered or free-tier key, since a stuck visit fires dozens of these in a burst and that is exactly what trips a per-minute quota. Leave it empty and the autopilot follows `provider` like everything else. `"vision_provider"` does the same for camera frames: it picks which backend LOOKS at a picture, separately from the one that talks. A turn carrying a frame only ever reaches a backend that can actually see it — Claude via the direct API (not the CLI fallback, which is text-only), Gemini, or LM Studio with `"lmstudio_vision": true` and a multimodal model loaded. If none can see, Jarvis says so rather than answering blind. |
| `persona` | Name, address term ("sir"), tone description, and a free-text `system_prompt_extra` for tweaks that don't need a code change |
| `voice` | Three interchangeable TTS backends behind one interface (`app/providers/tts.py`) — ElevenLabs, Fish Audio, and local Kokoro. Set `"tts_provider"` to pick which one is tried first, same failover behavior as `model.provider` above. Set `"tts_failover": false` to use **only** that one — which is what you want with local Kokoro, since falling over from a free local voice to a metered cloud one spends money exactly when you didn't ask it to. Kokoro reads `"kokoro_base_url"` (e.g. `http://127.0.0.1:8880`), `"kokoro_voice"` (`bf_emma`, `bf_isabella`, `bm_george`, `af_heart`, …) and `"kokoro_speed"`. |
| `wake_word` | The wake word itself, how long to wait for a command after a bare "Jarvis", and how long to wait for silence before treating a sentence as finished |
| `conversation` | Extra closing phrases to end conversation mode, and the spoken sign-off lines |
| `retrieval` | How many notes to retrieve per question, how many turns of history to keep |
| `images` | Max images per gallery lookup |
| `brain` | Radius and colors of the 3D brain visualization |

**Why long replies still start speaking immediately.** Synthesis cost is
linear in text length, so asking any backend for a whole paragraph at once
means waiting out the whole paragraph before the first word — a 434-character
reply measured 14.0s of silence. `viewer/js/controller/speechController.js`
splits the reply at sentence boundaries and pipelines the requests, so
playback begins after the *first* piece (~1.0s) while the rest is synthesized
behind it. Pieces start small and grow by `GROWTH` (1.8x) up to
`CHUNK_CHARS`, which is what keeps synthesis ahead of playback: Kokoro runs
~2.8x faster than real time, so a piece that is at most ~1.8x its predecessor
is always ready before the predecessor finishes playing. Measured on the same
reply: **14.0s to first word before, 1.0s after, with no gaps.** The pieces
are verbatim slices of the reply — the splitter works in offsets rather than
splitting and rejoining, so it can't turn "3 p.m." into "3 p. m.".

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
    anthropic_provider.py        Claude API call + CLI fallback
    gemini_provider.py           Google Gemini API call + CLI fallback
    cli_provider.py              CLI runner (Antigravity `agy` & Claude `claude` with configurable fallback)
    lmstudio_provider.py         local model on the LAN via LM Studio (OpenAI-compatible)
    elevenlabs_provider.py       ElevenLabs text-to-speech
    fish_audio_provider.py       Fish Audio text-to-speech
    kokoro_provider.py           local Kokoro text-to-speech (tools/kokoro_server.py)
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
| JARVIS uses the browser voice instead of a real one | None of the three TTS backends (ElevenLabs/Fish Audio/Kokoro) are configured, or all of them failed — check the server's console output. With Kokoro, check the voice box is up: `systemctl --user status kokoro-tts` and `curl http://127.0.0.1:8880/health`. |
| A `jf_`/`jm_` (Japanese) or `zf_`/`zm_` (Chinese) voice 500s | That language's G2P engine isn't installed. Japanese: `tools/setup_kokoro.sh --japanese`. Chinese: `.venv-kokoro/bin/pip install "misaki[zh]"`. Then `systemctl --user restart kokoro-tts`. Note these voices phonemize *their own* language — feeding English text to a Japanese pack produces Japanese-accented approximations, not English. |
| The voice pauses mid-reply | Synthesis has fallen behind playback. Raise `KOKORO_THREADS` (8 is the measured sweet spot on 16 cores; 16 is *far* slower than 8), or lower `GROWTH`/`CHUNK_CHARS` in `viewer/js/controller/speechController.js` so later pieces stay smaller. |
| Kokoro is silent or slow | `journalctl --user -u kokoro-tts -n 50`. The first request after a restart loads the model and takes a few seconds; later ones are faster than real time. Bump `KOKORO_THREADS` in `~/.config/systemd/user/kokoro-tts.service` if the machine has cores to spare. |
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
