# Jarvis-Full Project Overview

> Updated on: 2026-05-11

## Summary

**Jarvis-full** is an AI assistant infrastructure project. Its core is an Obsidian vault (`7oss`) serving as the **persistent long-term memory**, coupled with a Python-based **Voice Interaction Client** that acts as the primary interface for the user.

---

## Project Structure

```
/home/code-work/Jarvis-full/
├── 7oss/                        ← Obsidian vault (Knowledge Base)
│   ├── .obsidian/
│   │   └── plugins/
│   │       └── obsidian-local-rest-api/   ← API Layer
└── client/                      ← Voice Interaction Client (Interface)
    ├── main.py                  # Orchestration loop
    ├── modules/
    │   ├── obsidian.py          # Vault communication
    │   ├── llm_agent.py         # Jarvis AI logic & tool use
    │   ├── stt/                 # Speech-to-Text (Cloud/Local)
    │   └── tts.py               # Text-to-Speech (Cloud)
    └── README.md                # Client setup instructions
```

---

## Key Components

### 1. Obsidian Local REST API
Enables programmatic access to the vault.
- **Insecure Port:** `27123`
- **Secure Port:** `27124`
- **Capability:** Allows Jarvis to search, read, and write notes to maintain context.

### 2. Jarvis Voice Client
A Python application that provides the interactive loop:
- **STT (Speech-to-Text):** Uses OpenAI Whisper (Cloud) with a local fallback for high-accuracy voice transcription.
- **LLM Agent (Reasoning):** Powered by GPT-4o. It uses function calling to automatically interact with Obsidian based on user prompts.
- **TTS (Text-to-Speech):** Uses OpenAI TTS for natural-sounding responses.

---

## Architecture

```
       [ User ]
          │
      (Voice/Audio)
          ▼
 [ Jarvis Voice Client ] <───> [ OpenAI APIs ] (STT, LLM, TTS)
          │
  (Local REST API)
          ▼
  [ Obsidian Vault ]
  ├── Long-term Memory
  ├── Project Notes
  └── Personal Knowledge Base
```

The design pattern is: **Interface (Python) <-> Reasoning (LLM) <-> Memory (Obsidian)**.

---

## Git State

- Main branch pushed to GitHub.
- Includes Python client dependencies and configuration templates.

---

## Tags

#jarvis #project-overview #architecture #obsidian #voice-ai #stt #tts
