"""Engine adapters — implementations of core.ports.EngineAdapter.

ReplayEngine (deterministic, no GPU) and VllmEngine (real fleet, official
Prometheus telemetry) are substitutable behind one port (spec D4). Published
numbers come only from a real engine; replay exists for determinism and CI.
"""

from admitperf.engines.replay import REPLAY_CAPABILITIES, ReplayConfig, ReplayEngine

__all__ = ["REPLAY_CAPABILITIES", "ReplayConfig", "ReplayEngine"]
