#!/usr/bin/env bash
# Prove the deployed cluster actually serves, through the gateway.
#
#   make smoke
#
# Checks IDENTITY, not status codes. A 200 from the wrong process is worse than a
# connection refused, because it looks like success — a leftover mock server on local
# port 8000 once served three minutes of load at exactly the requested arrival rate.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=_env.sh
source "$ROOT/scripts/_env.sh"

GATEWAY="${GATEWAY:-http://127.0.0.1:8800}"
ENGINE="${ENGINE:-http://127.0.0.1:8801}"
MODEL="${MODEL:-lab}"
fails=0

ok()   { printf '  %-44s ok\n' "$1"; }
bad()  { printf '  %-44s %s\n' "$1" "$2"; fails=$((fails + 1)); }

echo "engine  $ENGINE"
body="$(curl -s -m 10 "$ENGINE/v1/models")"
if grep -q 'owned_by' <<<"$body"; then ok "is real vLLM, not a stand-in"; else bad "is real vLLM, not a stand-in" "got: ${body:0:60}"; fi
# The signals a KV policy reads. Absent means the trace would be empty and a report
# would say UNKNOWN rather than anything about the policy.
metrics="$(curl -s -m 10 "$ENGINE/metrics")"
for m in vllm:kv_cache_usage_perc vllm:num_requests_waiting vllm:num_requests_running; do
  grep -q "^$m" <<<"$metrics" && ok "exports $m" || bad "exports $m" MISSING
done

echo
echo "gateway $GATEWAY"
gw="$(curl -s -m 10 "$GATEWAY/metrics")"
grep -q '^orch_' <<<"$gw" && ok "is the gateway, not an engine" || bad "is the gateway, not an engine" "no orch_ metrics"

# One real request, end to end, through admission.
resp="$(curl -s -m 180 -X POST "$GATEWAY/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"say hi\"}],\"max_tokens\":8}")"
if grep -q '"content"' <<<"$resp"; then
  ok "serves a chat completion"
elif grep -q 'model_not_allowed' <<<"$resp"; then
  # SERVED_NAMES carries every --served-model-name; without the alias the gateway
  # refuses a model the engine behind it serves happily.
  bad "serves a chat completion" "model '$MODEL' not in the gateway allowlist"
elif grep -q 'prompt_too_long' <<<"$resp"; then
  bad "serves a chat completion" "guardrail MAX_PROMPT_CHARS is too low"
elif grep -q 'tenant_tokens' <<<"$resp"; then
  bad "serves a chat completion" "tenant rate limit shed it — set gateway.tenantTokensPerMin: 0"
else
  bad "serves a chat completion" "got: ${resp:0:80}"
fi

echo
if [[ $fails -gt 0 ]]; then echo "$fails check(s) failed"; exit 1; fi
echo "smoke pass — ready for: admitperf load $GATEWAY --model $MODEL --rps 4 --for 3m"
