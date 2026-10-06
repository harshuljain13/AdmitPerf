#!/usr/bin/env bash
# Copy the chart and lib/ to the box. .env is excluded — secrets reach the cluster as
# Secrets (see up.sh), never as files on disk or values in a manifest.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

rsync -az --delete -e "ssh -i $LAMBDA_SSH_KEY -o StrictHostKeyChecking=accept-new" \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  --exclude '.env' \
  "$ROOT/chart" "$ROOT/lib" "$ROOT/values" \
  "$LAMBDA:admitperf/"
echo "synced chart/ lib/ values/ -> $LAMBDA:~/admitperf/"
