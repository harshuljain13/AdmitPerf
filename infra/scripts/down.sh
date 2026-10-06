#!/usr/bin/env bash
# Remove the release and close the tunnel.
#
# Does NOT uninstall k3s and does NOT terminate the instance. Lambda bills by the hour
# and that is a decision for the console, not for a script someone runs to free a port.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

PIDFILE="$ROOT/.tunnel.pid"
if [[ -f "$PIDFILE" ]]; then
  while read -r pid; do kill "$pid" 2>/dev/null && echo "tunnel $pid closed"; done < "$PIDFILE"
  rm -f "$PIDFILE"
fi

"${SSH[@]}" "$LAMBDA" "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml && \
  sudo -E helm uninstall $RELEASE -n $NS 2>&1 | tail -2 || echo 'release not installed'"

echo
echo "k3s is still running and the instance is still billing."
echo "Terminate it in the Lambda console."
