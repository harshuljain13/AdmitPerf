"""Why a request was refused, and what the gateway should return.

A controlled vocabulary, not free text. Reports group by reason, and a reason
nobody else uses cannot be grouped with anyone else's — which is the whole
problem AdmitPerf exists to fix.

The status code is derived from the reason rather than chosen by the policy. That
is the part that makes two teams' refusals comparable without either of them
thinking about it: every AdmitPerf policy refusing for KV pressure produces a 503
with a Retry-After, in every gateway.

503 means the server cannot serve anyone right now. 429 means this caller is
asking for too much. Getting them backwards tells a client to retry when it
should back off.
"""

from __future__ import annotations

REASONS: dict[str, int] = {
    # the server cannot serve this now
    "kv_pressure": 503,
    "queue_depth": 503,
    "capacity_reserved": 503,
    "wait_estimate_exceeded": 503,
    "deadline_infeasible": 503,
    "predicted_length": 503,
    "policy_error": 503,
    # this caller is asking for too much
    "tenant_quota": 429,
    "rate_limit": 429,
}

ADMITTED = 200
