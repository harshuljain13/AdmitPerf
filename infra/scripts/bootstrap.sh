#!/usr/bin/env bash
# Put k3s and helm on the box. Run once per machine; idempotent after that.
#
# Separate from up.sh because it is the only step that touches the OS, takes minutes,
# and never needs repeating. up.sh calls it, so you do not have to.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

echo "== $LAMBDA"
"${SSH[@]}" "$LAMBDA" 'nvidia-smi --query-gpu=name,memory.total --format=csv,noheader'

"${SSH[@]}" "$LAMBDA" bash -se <<'REMOTE'
set -euo pipefail

if ! command -v k3s >/dev/null 2>&1; then
  echo "== k3s (nvidia default runtime) =="
  # --default-runtime nvidia, or every GPU pod needs an explicit runtimeClassName and
  # the ones that forget it schedule and then fail to see a device.
  curl -sfL https://get.k3s.io \
    | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644 --default-runtime nvidia" sh -
else
  echo "== k3s present =="
fi

if ! command -v helm >/dev/null 2>&1; then
  echo "== helm =="
  curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
else
  echo "== helm present: $(helm version --short) =="
fi

sudo k3s kubectl wait --for=condition=Ready node --all --timeout=300s
REMOTE
echo "bootstrap done"
