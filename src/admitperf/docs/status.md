# Status

What exists, what does not, and what has actually been measured. Updated by hand;
if it disagrees with the code, the code is right and this is stale.

## Measured results: none

There are no results in this repository.

Earlier runs were deleted rather than kept, and the reasoning is worth stating
because it is the same standard this project asks of published work. Those bundles
were produced by a harness that had not been validated, on a provider that has
since been removed, with a policy implementation that has since been deleted, and
through code paths that turned out to resolve directories that never existed.

A number is evidence only if the thing that produced it can be run again. None of
those could be, so keeping them would have meant keeping claims whose provenance
nobody could check — which is precisely the failure the survey behind this project
documents. Deleting them costs nothing real: a measurement that cannot be
reproduced was never a measurement.

The first result will come from `experiments/signal-liveness`, and it will say
either that the signal moved or that it did not. Both are reportable.

## Built and tested

| | Where | Tests |
| --- | --- | --- |
| Policy contract, with the taxonomy declared on the policy | `core/api.py` | yes |
| Policy registry, including third-party plugins | `core/registry.py` | yes |
| Three policies: `no_admission`, `kv_threshold`, `queue_depth` | `policies/` | yes |
| Cluster config to manifests and gateway environment | `infra/render.py` | yes |
| Refusals: bad quantization for the card, weights that leave no KV, under one sequence, slicing, oversubscribed host, TP across hosts, 429 overflow | `infra/render.py` | yes |
| The AdmitPerf Report: liveness verdict, policy card, seven reporting items | `report/` | yes |
| `report.json` as the artifact; text, markdown, HTML and the dashboard render from it | `report/schema.py` | yes |
| Experiment driver: load, decisions, report, against a fake engine | `bench/drive.py` | partly |
| Dashboard | `reports/dashboard/` | yes |

## Not built

| | Tracked as |
| --- | --- |
| Cluster brought up from a config, over SSH | [#15](https://github.com/harshuljain13/AdmitPerf/issues/15) |
| The vendored gateway reading a policy from the registry | [#10](https://github.com/harshuljain13/AdmitPerf/issues/10) |
| A run that fails when its signal never reached the threshold | [#12](https://github.com/harshuljain13/AdmitPerf/issues/12) |
| Load generators from the lab | [#27](https://github.com/harshuljain13/AdmitPerf/issues/27) |
| The research agent wired to the gateway | [#16](https://github.com/harshuljain13/AdmitPerf/issues/16) |

## Known limits

**The KV hop is instrumented, not real.** `router/kvbus.py` POSTs a metadata dict,
not KV tensors, so hop counts show that the request path split across two pools and
nothing more. No transfer latency can be claimed from this stack.
[#33](https://github.com/harshuljain13/AdmitPerf/issues/33).

**The experiment driver is new and lightly tested.** It was written small and
explicit on purpose, so that it can be read in full rather than trusted, but it has
not been validated check by check. Until it has, a number from it is provisional.

**The attention shape in the cluster config is declared, not read from the model.**
`kv_bytes_per_token`, and therefore every concurrency estimate, depends on it. It
needs checking against the model's `config.json` on first bring-up; wrong by 2x
moves every figure by 2x.

**Nothing has run on a GPU.** Every manifest `infra/render.py` produces is
unapplied, and the readiness probe in particular is unexercised.
