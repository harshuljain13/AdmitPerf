{{/*
Shared computations. Every number that two templates would otherwise each work out
for themselves lives here, because the bug this chart replaces was exactly that:
lambda_vllm.sh hardcoded gpu-memory-utilization 0.85 while the config said 0.90, and
omitted --max-num-seqs, the one flag that decides whether a KV policy can fire.
*/}}

{{- define "lab.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: Helm
{{- end }}

{{/* Every engine Deployment name, in pool order: vllm-engine-0, vllm-prefill-0, ... */}}
{{- define "lab.engineNames" -}}
{{- $names := list -}}
{{- range $pool := .Values.pools -}}
  {{- range $i := until (int $pool.replicas) -}}
    {{- $names = append $names (printf "vllm-%s-%d" $pool.name $i) -}}
  {{- end -}}
{{- end -}}
{{- join "," $names -}}
{{- end }}

{{/*
In-cluster URLs for the engines in the named pools, comma separated.

Falls back to EVERY engine when none of the named pools exist, which is what
aggregated mode means: the gateway's env contract knows only PREFILL_URLS and
DECODE_URLS, so both point at the same engines. The static orch-serve.yaml instead
hardcoded http://vllm-prefill:8000, so on an aggregated run the gateway came up
healthy pointing at a Service that did not exist.

Usage: {{ include "lab.poolUrls" (dict "root" . "pools" (list "prefill")) }}
*/}}
{{- define "lab.poolUrls" -}}
{{- $root := .root -}}
{{- $want := .pools -}}
{{- $urls := list -}}
{{- range $pool := $root.Values.pools -}}
  {{- if has $pool.name $want -}}
    {{- range $i := until (int $pool.replicas) -}}
      {{- $urls = append $urls (printf "http://vllm-%s-%d:%d" $pool.name $i (int $root.Values.engine.port)) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- if not $urls -}}
  {{- range $pool := $root.Values.pools -}}
    {{- range $i := until (int $pool.replicas) -}}
      {{- $urls = append $urls (printf "http://vllm-%s-%d:%d" $pool.name $i (int $root.Values.engine.port)) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- join "," $urls -}}
{{- end }}

{{/*
Bytes of KV per token: 2 (K and V) x layers x kv_heads x head_dim x dtype_bytes.
Grouped-query attention keeps kv_heads well below the attention head count, which is
why this comes out smaller than people expect — 56 KiB/token for a 7B here.
*/}}
{{- define "lab.kvBytesPerToken" -}}
{{- $a := .Values.model.attention -}}
{{- mul 2 (int $a.layers) (int $a.kvHeads) (int $a.headDim) (int $a.dtypeBytes) -}}
{{- end }}

{{/*
Prompt character ceiling for the guardrail. Characters, not tokens: ~5.5 chars/token
for digit filler and ~4 for English, so the context length times six cannot clip a
prompt the engine would itself accept. The guardrail's own default is 8,000, which
rejects every request at the length an admission experiment needs.
*/}}
{{- define "lab.maxPromptChars" -}}
{{- mul (int .Values.engine.maxModelLen) (int .Values.gateway.guardrail.maxPromptCharsPerToken) -}}
{{- end }}

{{/* All names the engine answers to, for the gateway's model allowlist. */}}
{{- define "lab.servedNames" -}}
{{- join "," (prepend .Values.model.servedNames .Values.model.id) -}}
{{- end }}
