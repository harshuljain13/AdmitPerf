#!/usr/bin/env bash
# Deploy the cluster. One command, idempotent, any topology.
#
#   bash scripts/up.sh                 values/single.yaml
#   VALUES=disagg bash scripts/up.sh
#
# Five steps, each one named. There is no ordering to remember because helm owns it:
# subcharts (HAMi, KEDA, kube-prometheus-stack) are dependencies with conditions, so a
# topology that does not want them never installs them.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "1/5  bootstrap"
bash "$ROOT/scripts/bootstrap.sh"

step "2/5  sync"
bash "$ROOT/scripts/sync.sh"

step "3/5  secrets"
# Secrets come from .env and are created as Secrets, so no key is ever rendered into a
# manifest. A key in a template shows up in `helm get values`, in CI logs, and in git.
if [[ -n "${HF_TOKEN:-}" ]]; then
  "${SSH[@]}" "$LAMBDA" "$KUBECTL create namespace $NS --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null; \
   $KUBECTL -n $NS create secret generic admitperf-hf \
    --from-literal=token='$HF_TOKEN' --dry-run=client -o yaml | $KUBECTL -n $NS apply -f -" >/dev/null
  echo "  admitperf-hf        (HF_TOKEN)"
fi
if [[ -n "${OVERFLOW_API_KEY:-}" ]]; then
  "${SSH[@]}" "$LAMBDA" "$KUBECTL -n $NS create secret generic admitperf-overflow \
    --from-literal=api_key='$OVERFLOW_API_KEY' --dry-run=client -o yaml | $KUBECTL -n $NS apply -f -" >/dev/null
  echo "  admitperf-overflow  (OVERFLOW_API_KEY)"
fi
[[ -n "${HF_TOKEN:-}${OVERFLOW_API_KEY:-}" ]] || echo "  none needed"

step "4/5  helm ($VALUES)"
"${SSH[@]}" "$LAMBDA" "cd admitperf && \
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml && \
  sudo -E helm dependency build chart >/dev/null && \
  sudo -E helm upgrade --install $RELEASE ./chart \
    --namespace $NS --create-namespace \
    -f values/${VALUES}.yaml \
    --wait --timeout 25m" 2>&1 | tail -20

step "5/5  status"
"${SSH[@]}" "$LAMBDA" "$KUBECTL -n $NS get pods -o wide"

printf '\n\033[1m== reachable\033[0m\n'
bash "$ROOT/scripts/tunnel.sh"
