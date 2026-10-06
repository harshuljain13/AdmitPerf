#!/usr/bin/env bash
# Forward every NodePort the chart exposes, and VERIFY WHAT ANSWERS.
#
# Lambda firewalls the public interface down to SSH, so this is the only way in.
#
# Two deliberate choices, both from things that went wrong:
#
#   - Ports start at 8800, not 8000. A leftover mock server held local 8000, the
#     forward failed to bind, and the bring-up reported the port reachable because
#     SOMETHING answered. Load then ran for three minutes against a fake engine at
#     exactly the requested arrival rate.
#
#   - The check reads the body, not the status code. A 200 from the wrong process is
#     worse than a connection refused, because it looks like success.
set -euo pipefail
source "$(dirname "$0")/_env.sh"

PIDFILE="$ROOT/.tunnel.pid"
if [[ -f "$PIDFILE" ]]; then
  while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDFILE"
  rm -f "$PIDFILE"
  sleep 1
fi

# Local:remote, read from the rendered chart rather than listed here, so a topology
# that deploys fewer components forwards fewer ports.
PORTS="$(helm template "$RELEASE" "$ROOT/chart" -n "$NS" -f "$VALUES_FILE" \
  | awk '/nodePort:/ {print $2}' | sort -un)"

FORWARDS=()
declare -a MAP
i=0
for rp in $PORTS; do
  case "$rp" in
    30080) lp=8800; what="gateway — drive load here" ;;
    31495) lp=31495; what="grafana" ;;
    30090) lp=30090; what="prometheus" ;;
    30030) lp=30030; what="open-webui" ;;
    *)     lp=$((8801 + i)); what="engine (metrics)"; i=$((i + 1)) ;;
  esac
  FORWARDS+=(-L "$lp:127.0.0.1:$rp")
  MAP+=("$lp|$rp|$what")
done

ssh -i "$LAMBDA_SSH_KEY" -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  "${FORWARDS[@]}" "$LAMBDA" || {
    echo "tunnel failed: a local port is already bound. Close it and re-run." >&2
    exit 1
  }
pgrep -f "ssh.*ExitOnForwardFailure.*$LAMBDA" | tail -1 > "$PIDFILE"
sleep 3

for row in "${MAP[@]}"; do
  IFS='|' read -r lp rp what <<<"$row"
  case "$what" in
    gateway*)    probe=/metrics;     expect=orch_ ;;
    engine*)     probe=/v1/models;   expect='owned_by' ;;
    grafana)     probe=/api/health;  expect=database ;;
    prometheus)  probe=/-/ready;     expect=Ready ;;
    *)           probe=/;           expect='' ;;
  esac
  body="$(curl -s -m 8 "http://127.0.0.1:$lp$probe" 2>/dev/null || true)"
  if [[ -z "$expect" ]]; then
    mark=$([[ -n "$body" ]] && echo ok || echo "no answer")
  elif grep -q "$expect" <<<"$body"; then
    mark=ok
  else
    # Named explicitly: this is the failure that cost three minutes of fake results.
    mark="WRONG SERVICE or down"
  fi
  printf '  http://127.0.0.1:%-6s %-28s %s\n' "$lp" "$what" "$mark"
done
