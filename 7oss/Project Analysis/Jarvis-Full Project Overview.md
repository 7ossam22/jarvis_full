# Jarvis-Full Project Overview

> Analyzed on: 2026-05-11

## Summary

**Jarvis-full** is an AI assistant infrastructure project. Its core is this Obsidian vault (`7oss`), which serves as the **persistent knowledge base and memory store** for an AI agent called "Jarvis". The vault is accessed programmatically by external tools (like Claude Code) via the Local REST API plugin.

---

## Project Structure

```
/home/code-work/Jarvis-full/
└── 7oss/                        ← Obsidian vault (this vault)
    ├── Welcome.md               ← Default placeholder note
    └── .obsidian/
        ├── core-plugins.json
        ├── community-plugins.json
        └── plugins/
            └── obsidian-local-rest-api/   ← Key plugin
                ├── main.js
                ├── manifest.json
                └── data.json              ← API config (ports, key, TLS certs)
```

---

## Key Component: Obsidian Local REST API

The single installed community plugin is **obsidian-local-rest-api**. This is the critical piece that enables programmatic access to the vault.

| Setting | Value |
|---|---|
| HTTP (insecure) port | `27123` |
| HTTPS (secure) port | `27124` |
| Insecure server | Enabled |
| Secure server | Enabled |
| API Key | Configured (in data.json) |
| TLS | Self-signed certificate (auto-generated) |

This plugin exposes a REST API so that external agents/scripts can:
- Read and write notes
- Search the vault
- Append/patch content
- List files and directories

---

## Git State

- Repository initialized at `/home/code-work/Jarvis-full`
- Remote: `origin` configured
- **No commits yet** — the repo is freshly scaffolded

---

## Current Vault State

The vault is essentially **empty** — only the default `Welcome.md` placeholder exists. All the actual Jarvis memory/knowledge content is yet to be built up.

---

## Architecture Intent

```
External Agent (Claude Code / AI)
        │
        │  HTTP REST API (port 27123 / 27124)
        ▼
Obsidian Local REST API Plugin
        │
        ▼
  7oss Vault (this vault)
  ├── Project notes
  ├── Agent memory
  ├── Task tracking
  └── Knowledge base
```

The design pattern is: **Obsidian = long-term memory for Jarvis**. Claude Code (or another AI agent) reads and writes notes here to maintain persistent context across sessions.

---

## Tags

#jarvis #project-overview #architecture #obsidian #ai-assistant
