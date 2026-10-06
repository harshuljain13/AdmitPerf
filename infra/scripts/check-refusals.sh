#!/usr/bin/env bash
# Every refusal the chart owes us, asserted. Runs on a laptop in a second.
#
# These are not style checks. Each one is a config that deploys cleanly, comes up
# healthy, and produces numbers that mean nothing — the most expensive kind of bug,
# because nothing about the run looks wrong.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/values/single.yaml"
fails=0

refuse() {
  local what="$1"; shift
  printf '  %-44s ' "$what"
  if helm template lab "$ROOT/chart" -n admitperf -f "$BASE" "$@" >/dev/null 2>&1; then
    echo "NOT REFUSED  <-- regression"
    fails=$((fails + 1))
  else
    echo refused
  fi
}

THREE_POOLS='pools=[{"name":"engine","replicas":1,"tensorParallelSize":1,"pipelineParallelSize":1,"gpus":1},{"name":"vision","replicas":1,"tensorParallelSize":1,"pipelineParallelSize":1,"gpus":1}]'

echo "refusals:"
# lib/gateway and lib/router are written against {prefill, decode} in ~15 places, so a
# third name is silently coerced into one of them.
refuse "a pool name the gateway cannot address" --set-json "$THREE_POOLS"
# Each engine does its own prefill and decode, so there is no handoff to carry.
refuse "a KV transport with no handoff"         --set kv.enabled=true
refuse "mode and pools disagreeing"             --set topology.mode=disaggregated
# A ScaledObject with no operator is inert and reports nothing.
refuse "autoscaling without KEDA"               --set autoscale.enabled=true
# An ignored gpumem request means the pod takes a whole card, so two slices are two cards.
refuse "slicing without the HAMi scheduler"     --set slicing.enabled=true
refuse "HAMi with nothing to slice"             --set hami.enabled=true
# Pointed at an engine, the browser would bypass the thing under test.
refuse "a chat UI bypassing the gateway"        --set ui.enabled=true --set gateway.enabled=false
# A runtime grants ACCESS to the card; a device plugin ADVERTISES it as schedulable.
# With neither, the node reports nvidia.com/gpu: 0 and every engine stays Pending —
# which is exactly how a clean install left the engine unschedulable with nothing in
# the plan hinting why.
refuse "no device plugin at all"                --set nvidia-device-plugin.enabled=false
# Two plugins advertising the same card give conflicting counts.
refuse "two device plugins on one card"         --set hami.enabled=true --set slicing.enabled=true

echo
echo "guarantees:"
assert() {
  local what="$1" pattern="$2"; shift 2
  printf '  %-44s ' "$what"
  # Captured, not piped. `grep -q` exits on the first match, helm takes SIGPIPE, and
  # under `pipefail` the pipeline then reports failure for a pattern that WAS found —
  # which made all eight of these look like regressions.
  local out
  out="$(helm template lab "$ROOT/chart" -n admitperf "$@" 2>/dev/null)"
  if grep -q "$pattern" <<<"$out"; then
    echo ok
  else
    echo "MISSING  <-- regression"
    fails=$((fails + 1))
  fi
}
# The static orch-serve.yaml hardcoded http://vllm-prefill:8000, so an aggregated run
# brought the gateway up healthy against a Service that did not exist.
assert "aggregated gateway points at its engine" 'value: "http://vllm-engine-0:8000"' -f "$ROOT/values/single.yaml"
assert "disagg gateway separates the pools"      'http://vllm-decode-0:8000'          -f "$ROOT/values/disagg.yaml"
# Its own default is 8,000 CHARACTERS; a prompt at the KV crossover is ~33,000, so
# every request came back 400 prompt_too_long — a refusal that reads like the policy.
assert "guardrail cannot clip the prompt"        'value: "196608"'                    -f "$ROOT/values/single.yaml"
# The code default is 10_000 tokens/min, under TWO requests at experiment length.
assert "tenant rate limit is off by default"     'name: TENANT_TOKENS_PER_MIN'        -f "$ROOT/values/single.yaml"
# vLLM's default is 256; omitting the flag moves the KV crossover by 4x.
assert "max-num-seqs reaches the engine"         '\--max-num-seqs'                    -f "$ROOT/values/single.yaml"
# READY 1/1 lies for minutes while torch.compile captures CUDA-graph buckets.
assert "engines are probed on /v1/models"        'path: /v1/models'                   -f "$ROOT/values/single.yaml"
# 1s, not the chart's 30s: KV fills and drains in seconds.
assert "prometheus samples fast enough"          'scrapeInterval: 1s'                 -f "$ROOT/values/single.yaml"
# A key in a template reaches `helm get values`, CI logs, and git.
assert "no API key is rendered into a manifest"  'secretKeyRef'                       -f "$ROOT/values/single.yaml" --set overflow.enabled=true

echo
if [[ $fails -gt 0 ]]; then
  echo "$fails check(s) failed"
  exit 1
fi
echo "all checks passed"
