#!/bin/bash
# One-command start: creates the venv + installs on first run, then starts the server.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
uvicorn main:app --host 127.0.0.1 --port 8000
