#!/usr/bin/env bash
# Follow a component's log.
#
#   bash scripts/logs.sh            the gateway — where admission decisions appear
#   WHAT=engine bash scripts/logs.sh
set -euo pipefail
source "$(dirname "$0")/_env.sh"

case "${WHAT:-gateway}" in
  gateway) SEL='app=orch-serve' ;;
  engine)  SEL='pool' ;;
  ui)      SEL='app=open-webui' ;;
  *) echo "WHAT must be gateway, engine or ui" >&2; exit 1 ;;
esac

exec ssh -i "$LAMBDA_SSH_KEY" -t "$LAMBDA" \
  "$KUBECTL -n $NS logs -l $SEL --all-containers --tail=200 -f"
