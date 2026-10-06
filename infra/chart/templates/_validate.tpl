{{/*
Refusals. Every one of these is a mistake that costs a weights download, a rented
hour, or worse — a run that completes and reports numbers that mean nothing.

`helm template` runs these, so they fail on a laptop in milliseconds.
*/}}

{{- define "lab.validate" -}}

{{/* ---- pool names are not free -------------------------------------------- */}}
{{- $names := list -}}
{{- range .Values.pools -}}{{- $names = append $names .name -}}{{- end -}}
{{- $sorted := sortAlpha $names -}}
{{- if and (ne (join "," $sorted) "engine") (ne (join "," $sorted) "decode,prefill") -}}
  {{- fail (printf "pools must be [engine] or [prefill, decode], not %v.\n\nlib/gateway and lib/router are written against those two names in ~15 places: metrics.py keys its dicts {\"prefill\",\"decode\"} and pools.py branches on `phase in (\"prefill\",\"both\")`. A third name is silently coerced into one of them, so the manifests would deploy a pool the gateway cannot address." $names) -}}
{{- end -}}

{{/* ---- the mode and the pools have to agree ------------------------------- */}}
{{- if and (eq .Values.topology.mode "disaggregated") (ne (join "," $sorted) "decode,prefill") -}}
  {{- fail "topology.mode is disaggregated but pools is not [prefill, decode]" -}}
{{- end -}}
{{- if and (eq .Values.topology.mode "aggregated") (ne (join "," $sorted) "engine") -}}
  {{- fail "topology.mode is aggregated but pools is not [engine]" -}}
{{- end -}}

{{/* ---- a KV transport without a handoff moves nothing --------------------- */}}
{{- if and .Values.kv.enabled (eq .Values.topology.mode "aggregated") -}}
  {{- fail "kv.enabled with topology.mode=aggregated. Each engine does prefill and decode for its own requests, so no handoff exists and the KV store would idle. This is what the old lambda_apply_slices.sh did: it deployed mooncake-store onto a one-GPU box with no second engine to hand KV to." -}}
{{- end -}}
{{- if and (eq .Values.topology.mode "disaggregated") (not .Values.kv.enabled) -}}
  {{- fail "topology.mode=disaggregated needs kv.enabled: prefill builds KV that decode must receive." -}}
{{- end -}}

{{/* ---- autoscaling needs its parts --------------------------------------- */}}
{{- if and .Values.autoscale.enabled (not .Values.keda.enabled) -}}
  {{- fail "autoscale.enabled needs keda.enabled: a ScaledObject with no KEDA operator is inert and reports nothing." -}}
{{- end -}}
{{- if and .Values.autoscale.enabled (not .Values.observability.enabled) -}}
  {{- fail "autoscale.enabled scales on orch_tokens_in_flight, which only Prometheus can supply. Set observability.enabled." -}}
{{- end -}}

{{/* ---- slicing needs the scheduler that honours it ------------------------ */}}
{{- if and .Values.slicing.enabled (not .Values.hami.enabled) -}}
  {{- fail "slicing.enabled needs hami.enabled: without the HAMi scheduler a gpumem request is ignored and the pod takes a whole card, so two 'slices' would quietly be two cards." -}}
{{- end -}}
{{- if and .Values.hami.enabled (not .Values.slicing.enabled) -}}
  {{- fail "hami.enabled without slicing.enabled installs a GPU slicer nothing uses. The old lambda_k3s_hami.sh did this unconditionally while the plan reported HAMi skipped." -}}
{{- end -}}

{{/* ---- something has to advertise the GPUs ------------------------------- */}}
{{- $ndp := index .Values "nvidia-device-plugin" -}}
{{- if and (not $ndp.enabled) (not .Values.hami.enabled) -}}
  {{- fail "no device plugin: with both nvidia-device-plugin.enabled and hami.enabled false the node advertises nvidia.com/gpu: 0 and every engine stays Pending with \"Insufficient nvidia.com/gpu\". A container runtime grants ACCESS to the card; a device plugin ADVERTISES it as schedulable. HAMi's plugin does both, so enable exactly one." -}}
{{- end -}}
{{- if and $ndp.enabled .Values.hami.enabled -}}
  {{- fail "nvidia-device-plugin.enabled AND hami.enabled: two plugins would both advertise nvidia.com/gpu on the same node and the counts conflict. HAMi supersedes it — turn nvidia-device-plugin off." -}}
{{- end -}}

{{/* ---- the chat UI must not bypass admission ------------------------------ */}}
{{- if and .Values.ui.enabled (not .Values.gateway.enabled) -}}
  {{- fail "ui.enabled needs gateway.enabled. Open WebUI points at the gateway so a browser request passes through admission; pointed straight at an engine it would bypass the thing under test." -}}
{{- end -}}

{{/* ---- a KV policy that cannot fire -------------------------------------- */}}
{{/*
   Not a refusal — whether KV binds depends on the request length, which lives in the
   experiment and not in this file. But if the threshold is unreachable at the FULL
   context then it is unreachable at every length, and that is knowable here.
*/}}
{{- $gpus := 0 -}}
{{- range .Values.pools -}}{{- $gpus = add $gpus (mul (int .replicas) (int .gpus)) -}}{{- end -}}
{{- if lt $gpus 1 -}}
  {{- fail "pools declare no GPUs" -}}
{{- end -}}

{{- end }}
