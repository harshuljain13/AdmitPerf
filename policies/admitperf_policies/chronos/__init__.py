"""Deadline-aware admission, in the spirit of Chronos.

Three pieces, separated so each is useful and testable on its own:

    estimator.py  engine counters -> current service rates (reusable by any
                  predictive policy: QLM and Fluid-WAIT need the same)
    wcrt.py       pure response-time arithmetic, no engine, no state
    policy.py     the AdmissionPolicy that wires them together
"""

from admitperf_policies.chronos.estimator import (
    ArrivalRateWindow,
    ServiceRateEstimator,
    ServiceRates,
    fit_cost_model,
)
from admitperf_policies.chronos.policy import ChronosInspiredWCRT
from admitperf_policies.chronos.wcrt import (
    AdmissionTest,
    CostModel,
    admission_test,
    theorem1_wcrt_ms,
    theorem3_max_decode_tasks,
)

__all__ = [
    "AdmissionTest",
    "ArrivalRateWindow",
    "ChronosInspiredWCRT",
    "CostModel",
    "ServiceRateEstimator",
    "ServiceRates",
    "admission_test",
    "fit_cost_model",
    "theorem1_wcrt_ms",
    "theorem3_max_decode_tasks",
]
