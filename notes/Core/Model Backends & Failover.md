# Model Backends & Failover

The **Model Backends & Failover** subsystem is what gives JARVIS its "brain".
Every backend implements one interface (`app/providers/llm.py` — `LLMProvider`,
`converse()`), so the rest of the app never knows or cares which one answered.
`model.provider` in `config.json` picks which is tried first; any other
configured backend is kept as automatic failover.

## LLM Backends

1. **Anthropic Claude (`AnthropicProvider`)**:
   - Direct API with `model.provider_api_key` + `model.model_id`.
   - Falls back to local CLI (`claude -p` or `agy -p`) when there's no API key or the API is unreachable.
   - Server-side web search tool.
2. **Google Gemini (`GeminiProvider`)**:
   - Direct API with `model.gemini_api_key` + `model.gemini_model_id`.
   - Falls back to local CLI (`agy -p` or `claude -p`) when unconfigured or on quota limit.
   - `google_search` grounding tool.
3. **Local CLI Backends (`CLIProvider` — Antigravity `agy` & Claude `claude`)**:
   - Direct CLI execution without requiring API keys. Selectable via `"provider": "agy"` or `"provider": "claude"`.
   - Configurable via `"model.cli_fallback"`: `"agy"`, `"claude"`, `"auto"` (default, with failover), or `"none"`.
4. **LM Studio — local model over the LAN (`LMStudioProvider`)**:
   - Talks to LM Studio's OpenAI-compatible "Local Server" at
     `model.lmstudio_base_url` (e.g. `http://192.168.1.50:1234/v1`).
   - Optional `lmstudio_model_id`, `lmstudio_api_key`, and
     `lmstudio_use_tools` (set false for models that reject a `tools` array).
   - Runs the full Gmail / Discord / Jira / browser / system connector tool
     loop. **No web search** — a local model has none.
   - Nothing leaves the LAN; fully offline once the model is loaded.

## Configuration

Configured in `config.json`:
```json
{
  "model": {
    "provider": "lmstudio",
    "lmstudio_base_url": "http://192.168.1.50:1234/v1",
    "lmstudio_model_id": "",
    "lmstudio_api_key": "lm-studio",
    "lmstudio_use_tools": true
  }
}
```

In LM Studio: load a model, start the server (Developer → Start Server), and
make sure "Serve on Local Network" is on so other machines on the LAN can
reach it. A tool-calling-capable model (e.g. Qwen, Llama 3.1+, Hermes) is
needed for the connectors to work.

## Related Systems

- [[Voice Synthesis & Multi-Key Failover]] — the same interface-plus-failover
  pattern, applied to text-to-speech.
- [[Safety Protocol & Guardrails]] — the destructive-action rules live in the
  shared system prompt, so they apply whichever backend is answering.
- [[Linux System Controller]] — one of the connector tool groups every backend
  can call.
