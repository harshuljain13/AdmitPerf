#!/usr/bin/env bash
# One-shot cluster bring-up — this is Step 13 in the README.
# Run on: GPU box. Wraps lambda_k3s_hami.sh (k3s + HAMi) followed by
# lambda_apply_slices.sh (the sliced workloads).
set -euo pipefail
CLUSTER="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$CLUSTER/.." && pwd)"
source "$CLUSTER/setup/_lambda_only.sh"
bash "$CLUSTER/setup/lambda_k3s_hami.sh"
bash "$CLUSTER/setup/lambda_apply_slices.sh"
