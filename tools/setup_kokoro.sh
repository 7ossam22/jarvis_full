#!/usr/bin/env bash
# tools/setup_kokoro.sh — install the local Kokoro voice (see tools/kokoro_server.py).
#
# Builds .venv-kokoro (CPU torch — Kokoro-82M is small, and this leaves the
# GPU alone), warms the model cache so the first "sir" isn't a 60-second wait,
# and installs a systemd --user service so the voice box comes back on login.
#
# Usage:  tools/setup_kokoro.sh              install + enable the service
#         tools/setup_kokoro.sh --no-service   just the venv and the model
#         tools/setup_kokoro.sh --japanese     also enable the jf_/jm_ voices
#
# --japanese pulls misaki[ja] and the UniDic MeCab dictionary (~800 MB on
# disk, downloaded separately from pip). It is opt-in because the English
# voices are the common case and that dictionary is by far the largest thing
# this installs. Without it a jf_/jm_ voice fails with "No module named
# 'pyopenjtalk'" or "Failed initializing MeCab".
set -euo pipefail

WITH_JAPANESE=0
NO_SERVICE=0
for arg in "$@"; do
  case "$arg" in
    --japanese)   WITH_JAPANESE=1 ;;
    --no-service) NO_SERVICE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv-kokoro"
PY="$VENV/bin/python"

echo "==> venv at $VENV"
[ -x "$PY" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip

echo "==> torch (CPU build)"
"$VENV/bin/pip" install --quiet --index-url https://download.pytorch.org/whl/cpu torch

echo "==> kokoro + server deps"
"$VENV/bin/pip" install --quiet kokoro "misaki[en]" soundfile fastapi "uvicorn[standard]"

if [ "$WITH_JAPANESE" = "1" ]; then
  echo "==> Japanese G2P (misaki[ja] + UniDic, ~800 MB)"
  "$VENV/bin/pip" install --quiet "misaki[ja]"
  # The unidic pip package ships a stub; the dictionary itself — mecabrc and
  # friends, which fugashi refuses to start without — is a separate download.
  if [ ! -f "$VENV/lib/python3.12/site-packages/unidic/dicdir/mecabrc" ]; then
    "$PY" -m unidic download
  fi
fi

echo "==> warming the model cache (downloads Kokoro-82M on first run)"
"$PY" - <<'PYEOF'
import warnings; warnings.filterwarnings("ignore")
from kokoro import KPipeline
import os
voice = os.environ.get("KOKORO_DEFAULT_VOICE", "bf_emma")
list(KPipeline(lang_code=voice[0], repo_id="hexgrad/Kokoro-82M")("Ready, sir.", voice=voice))
print("model ready")
PYEOF

if [ "$NO_SERVICE" = "1" ]; then
  echo "==> done. Start it by hand:  $PY $ROOT/tools/kokoro_server.py"
  exit 0
fi

UNIT="$HOME/.config/systemd/user/kokoro-tts.service"
echo "==> systemd --user service at $UNIT"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=Kokoro TTS (local voice for JARVIS)
After=network.target

[Service]
Type=simple
ExecStart=$PY $ROOT/tools/kokoro_server.py
Environment=KOKORO_HOST=127.0.0.1
Environment=KOKORO_PORT=8880
Environment=KOKORO_DEFAULT_VOICE=bf_emma
Environment=KOKORO_THREADS=8
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNITEOF

systemctl --user daemon-reload
systemctl --user enable --now kokoro-tts.service

echo "==> waiting for http://127.0.0.1:8880/health"
for _ in $(seq 1 60); do
  if curl -fsS -m 2 http://127.0.0.1:8880/health >/dev/null 2>&1; then
    curl -sS http://127.0.0.1:8880/health; echo; echo "==> Kokoro is up."
    exit 0
  fi
  sleep 1
done

echo "!! Kokoro did not answer in 60s. Logs:  journalctl --user -u kokoro-tts -n 50" >&2
exit 1
