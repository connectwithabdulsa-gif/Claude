#!/usr/bin/env bash
# DFY Sender for Mac/Linux: ./start.sh  (first run sets everything up)
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Install Python 3 from python.org first"; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -e .
[ -f .env ] || cp .env.example .env
echo "DFY Sender is starting. Keep this window open while sending."
exec .venv/bin/engine app
