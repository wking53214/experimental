"""
DGK with governed health limits.

DGK (github.com/wking53214/DGK) rejects a transaction when latency > 500, abort rate > 0.25 or re-entry
rate > 2.0. Those three numbers are hardcoded: nothing can lower them when the system is under stress, and
nothing stops a future change from raising them. Here they become boundaries in the governance core:

  * DGK reads them from the governor on every transaction (GovernedHealthLimitCheck replaces
    kernel.boundary_barrier; DGK itself is not modified).
  * A conservative policy asks for tighter limits when the kernel stays in a stressed regime
    (SustainedStressPolicy). The governor decides: floor, circuit breaker, audit.
  * Raising a limit goes through an operator, with the governor's grant and audit trail.

What this deliberately does NOT touch: DGK's regime recovery. DGK's regimes are state, and they recover
by themselves after three calm readings (its RECONSTRUCTION.md records that a regime that never
recovered was a bug). Only the LIMITS are one-way.

Divergences from stock DGK, on purpose (see integrations/DGK_FINDINGS.md):
  * A non-finite health reading fails closed (stock DGK commits a NaN).
  * Telemetry that stock DGK would crash on (negative or infinite values) is refused with the cause
    TELEMETRY_INVALID and recorded in DGK's audit trail, instead of raising out of process_transaction.

Duck-typed: nothing here imports dgk.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# (telemetry attribute, DGK fault name, boundary suffix, DGK's hardcoded value). Every limit is an UPPER
# bound: smaller is stricter, which is the orientation the governance core treats as tightening.
HEALTH_LIMITS = (
    ("latency", "latency_fault", "latency", 500.0),
    ("abort_rate", "abort_fault", "abort_rate", 0.25),
    ("reentry_rate", "reentry_fault", "reentry_rate", 2.0),
)
TELEMETRY_FIELDS = ("latency", "abort_rate", "reentry_rate", "load_depth", "determinism_index")
REGIME_SEVERITY = {"NOMINAL": 1, "TRANSIENT_SURGE": 2, "RESOURCE_SATURATED": 3,
                   "STOCHASTIC_CONFUSION": 4, "ANOMALOUS_DRIFT": 5}


class GovernedHealthLimitCheck:
    """Drop-in for dgk.interceptors.HealthLimitCheck whose limits live in the governor."""

    def __init__(self, governor, prefix: str = "dgk.health", defaults: Optional[Dict[str, float]] = None):
        self.governor, self.prefix = governor, prefix
        defaults = defaults or {}
        for attr, _fault, suffix, stock in HEALTH_LIMITS:
            # keeps a restored (lower) limit; a higher configured value is ignored and noted in the log
            governor.ensure_boundary(self.boundary_id(attr), "health_limit", float(defaults.get(attr, stock)))

    def boundary_id(self, attr: str) -> str:
        return f"{self.prefix}.{next(s for a, _f, s, _d in HEALTH_LIMITS if a == attr)}"

    def limits(self) -> Dict[str, float]:
        return {attr: float(self.governor.boundaries.get_boundary(self.boundary_id(attr)).current_limit)
                for attr, *_ in HEALTH_LIMITS}

    def verify_bounds(self, payload: Any) -> Tuple[bool, Dict[str, float]]:
        faults: Dict[str, float] = {}
        limits = self.limits()
        for attr, fault, _suffix, _default in HEALTH_LIMITS:
            value = getattr(payload, attr)
            if not math.isfinite(value) or value > limits[attr]:  # NaN fails closed
                faults[fault] = value
        return len(faults) == 0, faults


@dataclass
class SustainedStressPolicy:
    """Ask for tighter limits when the kernel stays in a stressed regime.

    The default is deliberately conservative, because the governor's automatic budget is small (3 steps)
    and false triggers spend it. This is POLICY: change it to suit your system.
    """
    min_severity: int = 4          # STOCHASTIC_CONFUSION or worse
    sustained: int = 10            # consecutive transactions at or above min_severity
    near: float = 0.8              # only metrics at or above this fraction of their limit are tightened
    factor: float = 0.9
    rearm_after_calm: int = 20     # consecutive calmer transactions before a new episode can trigger
    _run: int = field(default=0, init=False)
    _calm: int = field(default=0, init=False)
    _armed: bool = field(default=True, init=False)
    episode: int = field(default=0, init=False)

    def observe(self, regime: Optional[str], readings: Dict[str, float], limits: Dict[str, float]) -> List[str]:
        """Returns the telemetry attributes to tighten now (possibly none)."""
        severity = REGIME_SEVERITY.get(regime or "", 1)
        if severity >= self.min_severity:
            self._run += 1
            self._calm = 0
        else:
            self._run = 0
            self._calm += 1
            if self._calm >= self.rearm_after_calm:
                self._armed = True
        if self._run >= self.sustained and self._armed:
            self._armed = False
            self.episode += 1
            return [a for a in limits
                    if a in readings and math.isfinite(readings[a]) and readings[a] >= self.near * limits[a]]
        return []


def _telemetry_problem(telemetry_map: Dict[str, Any]) -> Optional[str]:
    for name in TELEMETRY_FIELDS:
        if name not in telemetry_map:
            continue
        try:
            v = float(telemetry_map[name])
        except (TypeError, ValueError):
            return f"telemetry field {name} is not a number"
        if not math.isfinite(v):
            return f"telemetry field {name} is not finite"
        if v < 0:
            return f"telemetry field {name} is negative"
    return None


class GovernedKernel:
    """Wraps a dgk.Kernel: governed limits, a stress policy, and fail-closed telemetry handling."""

    def __init__(self, kernel, governor, *, policy: Optional[SustainedStressPolicy] = None,
                 source: str = "dgk", credential: Optional[str] = None, prefix: str = "dgk.health"):
        self.kernel, self.governor, self.source, self.credential = kernel, governor, source, credential
        self.policy = policy or SustainedStressPolicy()
        self.check = GovernedHealthLimitCheck(governor, prefix)
        kernel.boundary_barrier = self.check
        self.signals_sent: List[dict] = []

    def process_transaction(self, partition_id, telemetry_map, text_payload, caller_id, caller_token) -> Dict[str, Any]:
        problem = _telemetry_problem(telemetry_map)
        if problem and self.kernel.callers.authorizes(caller_id, caller_token, partition_id):
            # an authorized caller sent telemetry stock DGK cannot process: refuse, and say so in DGK's trail
            self.kernel.audit_logger.append_record({
                "event": "refused", "partition_id": partition_id, "caller_id": caller_id,
                "timestamp": time.time(), "reason": problem, "cause": "TELEMETRY_INVALID"})
            return {"transaction_status": "REJECTED", "exception_details": problem, "cause": "TELEMETRY_INVALID"}
        result = self.kernel.process_transaction(partition_id, telemetry_map, text_payload, caller_id, caller_token)
        profile = result.get("stability_profile") or result.get("telemetry_metrics") or {}
        regime = profile.get("operational_regime")
        if regime is None or problem:
            return result
        readings = {a: float(telemetry_map.get(a, 0.0)) for a, *_ in HEALTH_LIMITS}
        replies = []
        for attr in self.policy.observe(regime, readings, self.check.limits()):
            reply = self.governor.propose_tightening(
                self.check.boundary_id(attr), source=self.source, credential=self.credential,
                factor=self.policy.factor, reason=f"sustained {regime}; {attr} near its limit",
                evidence={"regime": regime, "reading": readings[attr], "limit": self.check.limits()[attr],
                          "partition": partition_id, "episode": self.policy.episode},
                idempotency_key=f"{self.check.prefix}-episode-{self.policy.episode}-{attr}")
            replies.append(reply)
            self.signals_sent.append(reply)
        return {**result, "governance": replies} if replies else result
