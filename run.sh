#!/usr/bin/env bash
# Linux/macOS launcher: creates venv, installs deps, runs the app.
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "Создаю виртуальное окружение..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
exec python main.py "$@"
