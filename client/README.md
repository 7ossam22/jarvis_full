# Jarvis Voice Client

This is the interactive voice frontend for your Jarvis AI assistant. It connects to your Obsidian vault via the Local REST API plugin.

## Setup

1. **Install Dependencies:**
   ```bash
   pip install -r client/requirements.txt
   ```
   *Note: On Linux, you may need to install `portaudio19-dev` and `python3-pyaudio` for microphone support.*

2. **Configure Environment:**
   Create a `.env` file in the root directory (or in `client/`) with your OpenAI API key:
   ```env
   OPENAI_API_KEY=your_key_here
   STT_PROVIDER=cloud  # Options: cloud, local
   TTS_VOICE=alloy    # Options: alloy, echo, fable, onyx, nova, shimmer
   ```

3. **Obsidian Configuration:**
   Ensure Obsidian is open with the **Local REST API** plugin enabled. The client will automatically try to read the API key and ports from `7oss/.obsidian/plugins/obsidian-local-rest-api/data.json`.

## Usage

Run the main script:
```bash
export PYTHONPATH=$PYTHONPATH:.
python3 client/main.py
```

- **Listen:** Press Enter to start recording your voice.
- **Speak:** Jarvis will process your request, interact with your Obsidian vault if needed, and speak back to you.
- **Exit:** Press Ctrl+C.

## Features

- **Persistent Memory:** Jarvis uses your Obsidian vault as long-term memory.
- **Tool Use:** Jarvis can search your vault, read notes, and update/create new notes based on your conversation.
- **Switchable STT:** High-accuracy Cloud STT (Whisper) or Local fallback.
- **Natural Voice:** High-quality TTS for Jarvis's responses.
