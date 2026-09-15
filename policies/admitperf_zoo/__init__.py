"""AdmitPerf policy zoo — the policies under study.

Shipped separately from the library on purpose. Someone putting admission
control in front of a fleet installs `admitperf` and gets the decision path;
they do not get four research ports and the assumptions baked into them.

It also means the plugin mechanism is exercised by real code rather than only
by a test fixture: these register through the `admitperf.policies` entry-point
group, exactly as a third-party package would.

    pip install -e .          # the library
    pip install -e policies   # this zoo

`admitperf policies` lists whatever is installed. See
reports/policy-catalogue.md for what each one decides on.
"""

from admitperf_zoo.chronos import ChronosInspiredWCRT
from admitperf_zoo.kv_threshold import KVThreshold
from admitperf_zoo.queue_depth import QueueDepth, QueueDepthDefer

__all__ = ["ChronosInspiredWCRT", "KVThreshold", "QueueDepth", "QueueDepthDefer"]
