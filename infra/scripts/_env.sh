#!/usr/bin/env bash
# Resolve the box and the values file. Sourced by the other scripts; not run directly.
#
#   VALUES=disagg bash scripts/up.sh
#   HOST=2        bash scripts/up.sh     # LAMBDA_HOST_2
#
# .env holds LAMBDA_HOST_1..N and LAMBDA_SSH_KEY. Addresses and keys are facts about
# today, not about the topology, so they live there and never in a values file.

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
REPO="$(cd "$ROOT/.." && pwd)"

if [[ -f "$REPO/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO/.env"
  set +a
fi

VALUES="${VALUES:-single}"
VALUES_FILE="$ROOT/values/${VALUES}.yaml"
[[ -f "$VALUES_FILE" ]] || { echo "no such topology: $VALUES (see $ROOT/values/)" >&2; exit 1; }

HOST="${HOST:-1}"
VAR="LAMBDA_HOST_${HOST}"
LAMBDA="${!VAR:-}"
[[ -n "$LAMBDA" ]] || { echo "$VAR is not set in $REPO/.env" >&2; exit 1; }
[[ -n "${LAMBDA_SSH_KEY:-}" && -f "${LAMBDA_SSH_KEY:-}" ]] || {
  echo "LAMBDA_SSH_KEY is unset or not a file. Check $REPO/.env" >&2; exit 1; }

RELEASE="${RELEASE:-lab}"
# Its own namespace, not default. Both the nvidia-device-plugin and
# kube-prometheus-stack subcharts REFUSE to install into `default`, and a release
# spread across default also makes `helm uninstall` leave stragglers behind.
NS="${NS:-admitperf}"
REMOTE="\$HOME/admitperf"
SSH=(ssh -i "$LAMBDA_SSH_KEY" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
KUBECTL="sudo k3s kubectl"

export ROOT REPO VALUES VALUES_FILE LAMBDA LAMBDA_SSH_KEY RELEASE NS REMOTE KUBECTL
