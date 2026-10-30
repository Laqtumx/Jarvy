#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
if [[ ! -f .env ]]; then cp .env.example .env; fi
printf '\nJarvy installed. Activate with: source .venv/bin/activate\nRun: jarvy chat\nEdit .env to add GEMINI_API_KEY and configure allowed directories.\n'
