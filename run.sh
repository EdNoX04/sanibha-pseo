#!/usr/bin/env bash
# One-command local runner (macOS / Linux).
#   ./run.sh run            incremental generation
#   ./run.sh run --force    regenerate everything
#   ./run.sh test           offline test suite
#   ./run.sh keywords       list keywords; ./run.sh add "new keyword" [--run]
#   ./run.sh remove "keyword" [--run];  ./run.sh replace "old" "new" [--run]
# The API key is read from $GEMINI_API_KEY or .env; if neither exists you are prompted for it
# and it is kept in memory for this run only (never written to disk).
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. On macOS: brew install python@3.12" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment with $PY ..."
  "$PY" -m venv .venv
fi
if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
  echo "Installing dependencies (first run only, usually 1-3 minutes)..."
  .venv/bin/pip install --disable-pip-version-check --progress-bar on -r requirements.txt
  touch .venv/.deps-installed
fi

if [ "${1:-}" = "test" ]; then
  shift
  PYTHONPATH=src exec .venv/bin/python -m pytest -q "$@"
fi

needs_key=no
[ "${1:-}" = "run" ] && needs_key=yes
for a in "$@"; do [ "$a" = "--run" ] && needs_key=yes; done
if [ "$needs_key" = yes ] && [ -z "${GEMINI_API_KEY:-}" ] && ! grep -qs '^GEMINI_API_KEY=.\+' .env; then
  read -rsp "Gemini API key (not saved): " GEMINI_API_KEY; echo
  export GEMINI_API_KEY
fi

PYTHONPATH=src exec .venv/bin/python -m pseo "$@"
