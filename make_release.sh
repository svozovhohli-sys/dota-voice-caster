#!/usr/bin/env bash
# Build a clean archive to send to a friend (source only).
#
#   ./make_release.sh            -> dist/dota-voice-caster.zip
#
# The archive contains only the source code. .venv and models/ are NOT
# included: the friend's run.bat creates the venv, installs deps and the
# speech model is downloaded with --download-model.
set -euo pipefail
cd "$(dirname "$0")"

NAME="dota-voice-caster"
OUT="dist/${NAME}.zip"
mkdir -p dist
rm -f "$OUT"

zip -r "$OUT" \
  main.py run.sh run.bat requirements.txt README.md .gitignore \
  voicecaster \
  -x 'voicecaster/__pycache__/*' -x '*.pyc' \
  -x '.venv/*' -x 'models/*' -x 'dist/*' -x '*.log'

echo
echo "Готово: $OUT  ($(du -h "$OUT" | cut -f1))"
echo "Отправьте этот файл другу (Telegram/почта/Google Drive)."
echo "Либо, если есть git-репозиторий, отправьте: git bundle create ${NAME}.bundle --all"
