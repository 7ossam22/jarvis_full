#!/usr/bin/env python3
"""tools/kokoro_server.py — the local voice box behind app/providers/kokoro_provider.py.

Runs Kokoro-82M (hexgrad/Kokoro-82M) on this machine and exposes it at the
same OpenAI-compatible endpoint Kokoro-FastAPI serves — POST /v1/audio/speech
— which is exactly what the Kokoro TTS provider already speaks. No API key,
no per-character billing, no audio leaving the machine.

Runs OUTSIDE the stdlib-only JARVIS server because it needs torch + the kokoro
pip package — launch it with .venv-kokoro/bin/python (see tools/setup_kokoro.sh).
The JARVIS server reaches it over http://127.0.0.1:8880, set as
`voice.kokoro_base_url` in config.json.

Endpoints:
  POST /v1/audio/speech   {model, input, voice, response_format, speed} -> audio bytes
  GET  /v1/audio/voices   the voice packs available to this install
  GET  /health            readiness probe (also used by setup_kokoro.sh)

The first request for a given language loads a pipeline and the first request
for a given voice downloads its ~0.5 MB pack from the HF cache; both are then
held in memory, so only the very first synthesis pays that cost.
"""
import ctypes
import gc
import io
import os
import threading

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

SAMPLE_RATE = 24000  # Kokoro always outputs 24 kHz
DEFAULT_VOICE = os.environ.get("KOKORO_DEFAULT_VOICE", "bf_emma")

# response_format -> (libsndfile format, subtype, Content-Type). The provider
# asks for mp3; the rest are here because the OpenAI API defines them and the
# viewer plays whatever Content-Type comes back.
FORMATS = {
    "mp3":  ("MP3",  "MPEG_LAYER_III", "audio/mpeg"),
    "wav":  ("WAV",  "PCM_16",         "audio/wav"),
    "flac": ("FLAC", "PCM_16",         "audio/flac"),
    "opus": ("OGG",  "OPUS",           "audio/ogg"),
    "ogg":  ("OGG",  "VORBIS",         "audio/ogg"),
    "pcm":  (None,   None,             "audio/L16"),
}

# Torch defaults to every core and then thrashes badly on this model: on a
# 16-core box, 16 threads measured 52s for a passage 8 threads did in 8.1s.
# Measured on that machine: 2 threads 13.4s, 4 threads 8.7s, 8 threads 8.1s.
# Past 8 the per-layer work is too small to cover the sync cost.
torch.set_num_threads(int(os.environ.get("KOKORO_THREADS", "8")))

app = FastAPI(title="Kokoro TTS", docs_url=None, redoc_url=None)

_pipelines = {}
_pipelines_lock = threading.Lock()
# Kokoro's model is not re-entrant; one synthesis at a time keeps concurrent
# /speak calls from corrupting each other's audio.
_synth_lock = threading.Lock()

# Kokoro runs on CPU through glibc malloc, which happily keeps freed arenas
# mapped: Python drops the tensors but RSS never comes back down, so the
# process looks like it is leaking a few hundred MB per spoken reply. A
# collect + malloc_trim after each synthesis hands those arenas back to the OS.
try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:  # non-glibc (musl, macOS) - the collect alone still helps
    _libc = None


def _release_memory():
    """Drop synthesis garbage and return freed arenas to the OS."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    if _libc is not None and hasattr(_libc, "malloc_trim"):
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass


def _pipeline(lang_code):
    """The KPipeline for one language, built once and reused."""
    with _pipelines_lock:
        if lang_code not in _pipelines:
            from kokoro import KPipeline
            _pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
        return _pipelines[lang_code]


def _encode(audio, response_format):
    """Float32 samples -> (bytes, Content-Type) in the requested container."""
    fmt, subtype, content_type = FORMATS[response_format]
    if fmt is None:  # raw little-endian 16-bit PCM, no container
        return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes(), content_type
    buf = io.BytesIO()
    try:
        sf.write(buf, audio, SAMPLE_RATE, format=fmt, subtype=subtype)
    except Exception:
        # Not every libsndfile build carries every subtype (OPUS especially);
        # the container's own default beats failing the request.
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format=fmt)
    return buf.getvalue(), content_type


class SpeechRequest(BaseModel):
    input: str
    model: str = "kokoro"
    voice: str = DEFAULT_VOICE
    response_format: str = "mp3"
    speed: float = 1.0


@app.get("/health")
def health():
    return {"status": "ok", "default_voice": DEFAULT_VOICE,
            "loaded_languages": sorted(_pipelines)}


@app.get("/v1/audio/voices")
def voices():
    """The voice packs in this repo — from the HF cache when it has been
    populated, otherwise straight from the Hub."""
    try:
        from huggingface_hub import list_repo_files
        names = [f.split("/")[-1][:-3] for f in list_repo_files("hexgrad/Kokoro-82M")
                 if f.startswith("voices/") and f.endswith(".pt")]
        return {"voices": sorted(names)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"voice list unavailable: {e}")


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty input")

    response_format = (req.response_format or "mp3").lower()
    if response_format not in FORMATS:
        raise HTTPException(status_code=400,
                            detail=f"unsupported response_format {response_format!r}")

    voice = (req.voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    # A voice pack's first letter is its language: bm_george -> 'b' (British
    # English), af_heart -> 'a' (American), and so on.
    lang_code = voice[0]

    try:
        pipeline = _pipeline(lang_code)
        with _synth_lock:
            # inference_mode stops torch building autograd graphs for a model
            # that is never trained here - without it every reply leaves its
            # activation graph behind and RSS climbs for the life of the process.
            with torch.inference_mode():
                chunks = [audio.numpy().copy() for _, _, audio in
                          pipeline(text, voice=voice, speed=req.speed or 1.0)]
    except Exception as e:
        _release_memory()
        raise HTTPException(status_code=500, detail=f"synthesis failed: {e}")

    if not chunks:
        raise HTTPException(status_code=500, detail="synthesis produced no audio")

    body, content_type = _encode(np.concatenate(chunks), response_format)
    del chunks
    _release_memory()
    return Response(content=body, media_type=content_type)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,
                host=os.environ.get("KOKORO_HOST", "127.0.0.1"),
                port=int(os.environ.get("KOKORO_PORT", "8880")),
                log_level=os.environ.get("KOKORO_LOG_LEVEL", "warning"))
