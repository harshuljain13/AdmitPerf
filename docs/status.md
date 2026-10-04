# Status

**Measured results: none.** Nothing in this repository has been run against real
hardware in a way that produced a result worth citing. Earlier figures were
removed rather than carried forward, because they could not be reproduced.

## What exists

| | |
|---|---|
| `infra/` | a worked example of a host: cluster configs, a renderer with real refusals (fp8 on sm80, weights leaving no KV, TP split across hosts), and the bring-up scripts. Not part of the package. |
| the survey | `research/survey` — the taxonomy, the applicability table across sixteen admission-primary papers, and the seven reporting items |

## What is being built

The package, as three things: metrics in, signals derived, policies decide. Then
a report that leads with whether the policy could have fired at all.

## The finding that motivates all of it

Across sixteen admission-primary papers, six of the seven reporting items are
populated. The seventh — **signal liveness**, the observed range of the quantity a
policy reads — is empty for every one of them. So no reader can tell which
published results describe a policy acting and which describe a policy that never
got the chance.

The arithmetic matters here as much as the literature. On one A100-40GB serving
Qwen2.5-7B, the KV pool holds roughly 384k tokens. With `max_num_seqs=64` and a
2,168-token request, resident tokens cap at ~139k — so `kv_used_fraction` cannot
exceed about 0.35, and a policy thresholded at 0.90 is unreachable at any arrival
rate. A run like that reports numbers indistinguishable from no policy at all.
