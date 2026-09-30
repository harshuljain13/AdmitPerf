#!/usr/bin/env bash
# Copy the lab from your Mac to ~/admitperf-cluster on the GPU box, over rsync/SSH.
# Run on: Mac. Reads LAMBDA and LAMBDA_SSH_KEY from .env and exits 1 without them.
# Excludes .venv, __pycache__, .pytest_cache — source only, no environments.
set -euo pipefail

CLUSTER="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$CLUSTER/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

if [[ -z "${LAMBDA:-}" || -z "${LAMBDA_SSH_KEY:-}" || ! -f "$LAMBDA_SSH_KEY" ]]; then
  echo "Set LAMBDA and LAMBDA_SSH_KEY in .env" >&2
  exit 1
fi

echo "Sync Mac → $LAMBDA:~/admitperf-cluster/"
ssh -i "$LAMBDA_SSH_KEY" -o StrictHostKeyChecking=accept-new "$LAMBDA" "mkdir -p ~/admitperf-cluster"
rsync -avz -e "ssh -i $LAMBDA_SSH_KEY -o StrictHostKeyChecking=accept-new" \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.env' \
  ./ \
  "$LAMBDA:~/admitperf-cluster/"
echo "Synced."
