#!/usr/bin/env bash
# Ubuntu VM / WSL setup for FinEdge LinkedIn AutoGen + n8n
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MS_DIR="${PROJECT_ROOT}/microservice"

echo "==> Project root: ${PROJECT_ROOT}"

if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  echo "==> Installing OS packages with sudo"
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-venv python3-pip curl ca-certificates
  if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
  fi
  if ! command -v n8n >/dev/null 2>&1; then
    sudo npm install -g n8n
  fi
else
  echo "==> sudo unavailable; using user-space uv for Python"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "${HOME}/.local/bin/env"
  fi
  echo "Install Node.js 20+ and n8n on the course VM with sudo when available:"
  echo "  sudo apt install -y nodejs && sudo npm install -g n8n"
fi

echo "==> Creating Python environment"
cd "${MS_DIR}"
if command -v uv >/dev/null 2>&1; then
  uv venv --clear .venv
  uv pip install -r requirements.txt --python .venv/bin/python
elif python3 -m venv --help >/dev/null 2>&1; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
else
  echo "Neither uv nor python3-venv is available. Install python3-venv or uv."
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created microservice/.env — set OPENAI_API_KEY (or MOCK_LLM=true for smoke tests)."
fi

echo "==> Setup complete"
echo "Start API:  cd microservice && source .venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000"
echo "Mock mode:  MOCK_LLM=true uvicorn main:app --host 0.0.0.0 --port 8000"
echo "Start n8n:  n8n start"
echo "Import:     n8n/linkedin_content_workflow.json"
echo "Smoke:      microservice/.venv/bin/python scripts/e2e_dry_run_sim.py"
