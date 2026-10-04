"""Decision — and the response code it standardizes.

The point of these: a policy never picks a status code, so two teams' refusals are
comparable without either of them thinking about it.
"""

from __future__ import annotations

import pytest

from admitperf.core.decision import Decision
from admitperf.core.reasons import REASONS
from admitperf.core.verdict import Verdict


def test_status_is_derived_from_the_reason() -> None:
    assert Decision(Verdict.REJECT, "kv_pressure").status == 503
    assert Decision(Verdict.REJECT, "tenant_quota").status == 429
    assert Decision(Verdict.ADMIT).status == 200


def test_capacity_is_503_and_client_causes_are_429() -> None:
    """Semantic, not stylistic. 429 tells a caller to slow down; 503 says the server
    cannot serve anyone. Backwards tells a client to retry when it should back off."""
    assert REASONS["kv_pressure"] == 503
    assert REASONS["queue_depth"] == 503
    assert REASONS["rate_limit"] == 429
    assert REASONS["tenant_quota"] == 429


def test_a_free_text_reason_is_refused() -> None:
    """Reports group by reason, and a reason nobody else uses cannot be grouped with
    anyone else's — which is the problem this package exists to fix."""
    with pytest.raises(ValueError, match="not in the vocabulary"):
        Decision(Verdict.REJECT, "because it felt busy")


def test_a_refusal_needs_a_reason() -> None:
    with pytest.raises(ValueError, match="not in the vocabulary"):
        Decision(Verdict.REJECT)


def test_an_admitted_request_has_no_reason() -> None:
    with pytest.raises(ValueError, match="no refusal reason"):
        Decision(Verdict.ADMIT, "kv_pressure")


def test_admitted_is_the_question_a_gateway_asks() -> None:
    """A gateway with no queue cannot honour a defer and should not need to import an
    enum to discover that."""
    assert Decision(Verdict.ADMIT).admitted
    assert not Decision(Verdict.DEFER, "queue_depth", 200).admitted
    assert not Decision(Verdict.REJECT, "kv_pressure").admitted


def test_status_cannot_be_set_so_it_cannot_drift() -> None:
    """A stored status would be a second place the truth lives."""
    d = Decision(Verdict.REJECT, "kv_pressure")
    with pytest.raises(AttributeError):
        d.status = 418  # type: ignore[misc]
