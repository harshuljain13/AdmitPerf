"""Deadline-aware admission, in the spirit of Chronos.

Three pieces, separated so each is useful and testable on its own:

    estimator.py  engine counters -> current service rates (reusable by any
                  predictive policy: QLM and Fluid-WAIT need the same)
    wcrt.py       pure response-time arithmetic, no engine, no state
    policy.py     the AdmissionPolicy that wires them together
"""

from admitperf.policies.chronos.estimator import ServiceRateEstimator, ServiceRates
from admitperf.policies.chronos.policy import ChronosInspiredWCRT
from admitperf.policies.chronos.wcrt import Prediction, predict

__all__ = [
    "ChronosInspiredWCRT",
    "Prediction",
    "ServiceRateEstimator",
    "ServiceRates",
    "predict",
]
