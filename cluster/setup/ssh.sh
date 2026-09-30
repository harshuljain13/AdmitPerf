#!/usr/bin/env bash
# Open an interactive shell on the GPU box, already cd'ed into ~/admitperf-cluster.
# Run on: Mac. Reads LAMBDA and LAMBDA_SSH_KEY from .env, so that file must
# exist even though you do not source .env into your own shell until Step 19.
set -euo pipefail

CLUSTER="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$CLUSTER/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi
exec ssh -i "$LAMBDA_SSH_KEY" \
  -o StrictHostKeyChecking=accept-new \
  -o ExitOnForwardFailure=no \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=6 \
  -L 8000:127.0.0.1:8000 \
  -L 8001:127.0.0.1:8001 \
  -L 8080:127.0.0.1:8080 \
  -L 50051:127.0.0.1:50051 \
  -L 30030:127.0.0.1:30030 \
  -L 30080:127.0.0.1:30080 \
  -L 31495:127.0.0.1:31495 \
  -t "$LAMBDA" \
  'mkdir -p ~/admitperf-cluster; cd ~/admitperf-cluster; exec bash -l'
