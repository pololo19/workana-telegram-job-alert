#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Override: VENV_DIR=.venv311 ./run.sh
if [[ -z "${VENV_DIR:-}" ]]; then
  if [[ -x .venv311/bin/python ]]; then
    VENV_DIR=.venv311
  else
    VENV_DIR=.venv
  fi
fi

PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
  else
    PY=python3
  fi
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating virtual environment in $VENV_DIR ($PY)..."
  "$PY" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install -U pip
  "$VENV_DIR/bin/pip" install -r requirements.txt
fi

exec "$VENV_DIR/bin/python" bot.py
