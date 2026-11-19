"""
STACK trap events -> governed limits.

The ≡TACK kernel's P3.2 layer records a trap event whenever a boundary refuses or halts a payload (schema in
STACK-Kernel manual/sections/p3.2_schema_design.md). The research proposal (p3-2-governance-research) says
those events are telemetry that may tighten the next boundary, and that tightening may be automatic while
loosening never is. This module is that adaptation step, on top of the governance core:

    trap events (Sentinel's analysis, or a stream)  ->  interpretation policy  ->  Governor.propose_tightening

and the kernel reads the governed per-agent limit (GET /boundaries/<id>, or Governor.boundary_status) before
its next execution. The kernel itself is not modified and nothing here is Rust or C++.

Per-agent boundaries, one per numeric limit (every one an UPPER bound, so smaller is stricter):

    deadline_exceeded -> stack.agent.<agent>.deadline_ns
    token_exhausted   -> stack.agent.<agent>.tokens_capacity
    memory_exceeded   -> stack.agent.<agent>.memory_capacity_bytes

Not covered: capability_denied and signal_delivered have no numeric limit (a capability mask is a SET, and
this core only orders numbers), so they are counted as "unmapped" and change nothing.
"""
from __future__ import annotations

import hashlib
import math
import statistics
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

from src.governance.validation import ValidationOutcome

_OUTCOME = {"IMPROVED": ValidationOutcome.IMPROVED, "UNCHANGED": ValidationOutcome.UNCHANGED,
            "DEGRADED": ValidationOutcome.DEGRADED, "UNKNOWN": ValidationOutcome.UNKNOWN}

REASON_TO_LIMIT = {
    "deadline_exceeded": ("deadline_ns", "deadline_ns"),
    "token_exhausted": ("tokens_capacity", "tokens_capacity"),
    "memory_exceeded": ("memory_capacity_bytes", "memory_capacity_bytes"),
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass
class TrapPolicy:
    """The interpretation layer's default: tighten only on a repeated pattern, not on a single trap.

    min_traps trap events of the same reason for the same agent among that pair's last window_events events. This is
    POLICY and the proposal's open question B: change it to suit the system.
    """
    min_traps: int = 5
    window_events: int = 50
    factor: float = 0.9
    count_outcomes: tuple = ("RETRY", "TERMINAL_BREACH", "HALT")
    # Rate rule (off by default). With min_rate set, tightening also needs the traps among the agent's last
    # rate_window_tasks transactions to be at least min_rate of them. The kernel must report activity through
    # TrapBridge.observe_transactions; without it no rate can be computed and nothing tightens (fails safe).
    min_rate: Optional[float] = None
    rate_window_tasks: int = 2000
    # Per-class rate thresholds (docs/TRACE_TEST_PLAN.md, sixth round): an operator who knows an agent class's natural
    # tail sets its threshold above it. Keys are whatever TrapBridge(agent_class=...) returns for an agent; a class not
    # listed, or no agent_class callable, uses min_rate. Operator knowledge, not learned.
    min_rate_by_class: Optional[Dict[str, float]] = None
    # Drift rule (off by default). A high trap rate while the agent's COMPLETED tasks have also slowed against its own
    # baseline looks like a regression, not a runaway: hold instead of tightening, and let a person decide. The
    # baseline is the median of the first baseline_reports completed-duration medians, then frozen. Needs
    # observe_transactions(..., completed_median_ns=...). An agent that was slow from the start has no earlier
    # baseline to drift from and is treated as before. TRADE-OFF (docs/TRACE_TEST_PLAN.md): an attacker that slows its
    # completed tasks after the baseline is learned and before it hangs tasks is held for the whole attack (T15).
    drift_factor: Optional[float] = None
    baseline_reports: int = 5
    drift_recent_reports: int = 3
    # Excess-rate release (off unless drift_excess is True; needs completed_quantiles in observe_transactions). While
    # drifting, ask what share of tasks the agent's OLD duration distribution, scaled by the observed slowdown, would
    # push past the deadline. If the observed deadline-trap share over the last excess_window slices exceeds that
    # by max(excess_abs, excess_rel x explained), the slowdown does not explain the traps: release the hold.
    drift_excess: bool = False
    excess_window: int = 4
    excess_abs: float = 0.02
    excess_rel: float = 0.5
    # Which trap reasons may trigger tightening (None = all mapped reasons). The trace replays found that tightening
    # token and memory caps added collateral and no benefit; ("deadline_exceeded",) enforces only the deadline.
    reasons: Optional[tuple] = None
    # Validation layer (off by default; docs/TRACE_TEST_PLAN.md, fifth round). While an agent's deadline is tightened,
    # the kernel keeps canary_share of its tasks on the ORIGINAL deadline and reports both groups' counts through
    # observe_transactions(..., canary_tasks=, canary_deadline_traps=). Every validate_after slices the bridge
    # compares the main and canary trap shares: delta = main - canary is the collateral (tasks newly killed that
    # would have completed), cost = delta x D1, benefit_max = canary_share x (D0 - D1). DEGRADED if cost confidently exceeds
    # benefit_max (files a restore request for a person and holds further tightening), IMPROVED if cost is under
    # improved_ratio x benefit_max, UNCHANGED when the bounds do not separate, UNKNOWN under min_canary_tasks.
    validate_after: Optional[int] = None
    canary_share: float = 0.10
    improved_ratio: float = 1.0
    min_canary_tasks: int = 50
    # Confidence bounds on the verdict (added after the first run issued noise-driven verdicts; docs/TRACE_TEST_PLAN.md,
    # round 5): shares get normal-approximation bounds at review_z standard errors (rule of three for a zero count),
    # over every slice since the tightening up to review_window slices. DEGRADED needs the cost's lower bound above
    # the benefit's upper bound; IMPROVED needs the cost's upper bound under improved_ratio x the benefit's lower bound.
    review_z: float = 2.0
    review_window: int = 32
    # Canary selection key (round 8, T16). Without a key the kernel picks every round(1/share)th task by sequence
    # number, which an agent can predict and game (complete the canaries, hang the rest: a correct tightening then
    # reads DEGRADED and a person is asked to undo it). With a key the kernel picks tasks by a keyed hash of
    # (key, agent, seq) that the agent cannot predict. Set one in production.
    canary_key: Optional[str] = None
    # Cost budget (round 17; off by default). The worst-case certificate (docs/CERTIFICATE.md) says an honest agent can lose
    # at most the share of its tasks that finish between the new deadline and the old one. With cost_budget set (a share
    # of tasks: 0.01, 0.05, 0.10, 0.25 or 0.50) a deadline tightening is clamped so that, by the agent's own history of
    # completed durations, that share stays under the budget: the new deadline never goes below the (1 - budget) quantile of
    # what the agent completes. Until cost_budget_min_reports slices of completed quantiles are known, nothing tightens
    # (fails safe, like the rate rule). Operator-visible trade-off: an agent that completes slowly keeps a deadline near its
    # old one, so it also gives up most of the saving; its hung tasks are still killed at the old deadline.
    cost_budget: Optional[float] = None
    cost_budget_min_reports: int = 5
    cost_budget_recent_reports: int = 8
    # Provisional floor for a new identity (round 20; off by default, None). While the agent has fewer than
    # cost_budget_min_reports slices of history, a deadline tightening may go no lower than this share of the configured
    # deadline (for example 0.9: one policy step) instead of being refused. Round 19 found that refusing lets an attacker
    # who takes a new identity every few slices escape the limiter entirely (2.1% saved against 22.3%); this caps what a
    # new identity can cost an honest agent (the share of its tasks in the top (1 - ratio) of the deadline) without
    # waiting for history.
    cost_budget_new_identity_ratio: Optional[float] = None
    # Class-conditioned deadlines (round 23; off by default). A separate deadline per (agent, tool class), kept as a governed
    # boundary stack.agent.<agent>.class.<class>.deadline_ns. Each time the kernel calls refresh_class_deadlines, a class with at
    # least class_min_completions completed durations gets the target class_margin x its own 99th percentile, clamped to
    # [class_floor x D, D], and the boundary moves there ONLY if that is lower (one way: a rise needs a named operator through
    # request_loosening). The floor makes the certificate's saving ceiling 1 - class_floor (docs/CERTIFICATE.md, class section).
    class_deadlines: bool = False
    class_margin: float = 1.5
    class_floor: float = 0.25
    class_min_completions: int = 30
    class_history: int = 5000          # completed durations kept per class (oldest dropped)
    class_max_per_agent: int = 64      # classes tracked per agent (a flood of invented class names is ignored past this)

    def __post_init__(self):
        if self.class_deadlines and not (0.0 < self.class_floor <= 1.0 and self.class_margin >= 1.0
                                         and self.class_min_completions >= 1):
            raise ValueError("class_deadlines needs 0 < class_floor <= 1, class_margin >= 1, class_min_completions >= 1")
        if self.cost_budget is not None and self.cost_budget not in COST_BUDGET_POINT:
            raise ValueError(f"cost_budget must be one of {sorted(COST_BUDGET_POINT)}, got {self.cost_budget!r}")
        r = self.cost_budget_new_identity_ratio
        if r is not None and (self.cost_budget is None or not (0.729 <= r <= 1.0)):
            raise ValueError("cost_budget_new_identity_ratio needs cost_budget and must lie in [0.729, 1.0]")


# budget -> index into TrapBridge.QUANTILE_POINTS (0.50, 0.75, 0.90, 0.95, 0.99): the smallest point at or above 1 - budget,
# so the floor is never lower than the budget needs. Budgets under 1% are not offered: the kernel reports no finer quantile.
COST_BUDGET_POINT = {0.01: 4, 0.05: 3, 0.10: 2, 0.25: 1, 0.50: 0}


def _quantile(obs, q: float) -> float:
    """Linear-interpolated quantile (same convention as numpy's default)."""
    xs = sorted(obs); n = len(xs)
    if n == 1:
        return xs[0]
    pos = q * (n - 1); lo = int(math.floor(pos)); hi = min(lo + 1, n - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _agent_key(agent_id: Any) -> Optional[str]:
    if not isinstance(agent_id, str) or not agent_id:
        return None
    # "h-" is reserved for hashed keys: an ASCII id spelled like a victim's hashed key is hashed again
    # instead of landing on the victim's boundary (red team finding S4, docs/RED_TEAM.md).
    if _SAFE_ID.match(agent_id) and not agent_id.startswith("h-"):
        return agent_id
    return "h-" + hashlib.sha256(agent_id.encode("utf-8", "replace")).hexdigest()[:24]


class TrapBridge:
    def __init__(self, governor, *, defaults: Dict[str, float], policy: Optional[TrapPolicy] = None,
                 source: str = "stack-sentinel", credential: Optional[str] = None, max_agents: int = 1000,
                 is_expected: Optional[Callable[[dict], bool]] = None,
                 agent_class: Optional[Callable[[str], Optional[str]]] = None):
        """defaults: the configured starting limit for each of deadline_ns, tokens_capacity, memory_capacity_bytes
        (a trap event's own execution context may supply a per-agent capacity, which is used instead)."""
        missing = {s for _r, (s, _f) in REASON_TO_LIMIT.items()} - set(defaults)
        if missing:
            raise ValueError(f"defaults missing: {sorted(missing)}")
        self.gov, self.defaults = governor, dict(defaults)
        self.policy, self.source, self.credential = policy or TrapPolicy(), source, credential
        self.max_agents, self.is_expected, self.agent_class = max_agents, is_expected, agent_class
        self._recent: Dict[str, Deque[str]] = defaultdict(lambda: deque(maxlen=self.policy.window_events))
        self._stamps: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=self.policy.window_events))
        self._medians: Dict[str, list] = defaultdict(list)   # completed-duration medians reported per agent
        self._baseline: Dict[str, float] = {}
        self._quantiles: Dict[str, list] = defaultdict(list)   # per-slice completed-duration quantile vectors
        self._base_q: Dict[str, list] = {}                      # frozen baseline quantile vector
        self._slices: Dict[str, Deque[list]] = defaultdict(lambda: deque(maxlen=64))   # [tasks, deadline traps]
        self._drifting: set = set()
        self._slow_cache: Dict[tuple, float] = {}
        self._tallied: Dict[str, Deque[str]] = defaultdict(lambda: deque(maxlen=4096))   # trap ids already in the slice tally
        self._reviews: Dict[str, dict] = {}        # agent -> pending review of its deadline tightening
        self._restore_pending: set = set()         # boundary ids with a DEGRADED verdict awaiting a person
        self.verdicts: List[dict] = []             # every verdict issued, in order (also in the audit log)
        self._tasks: Dict[str, int] = defaultdict(int)   # transactions reported per agent (see observe_transactions)
        self._agents: set = set()
        self._class_ready: set = set()               # class boundaries already ensured
        self._class_obs: Dict[tuple, Deque[float]] = {}   # (agent key, class key) -> completed durations (ns)
        self._tightened_boundaries: set = set()   # boundaries this bridge has tightened: never evicted
        self._last_seen: Dict[str, int] = {}      # agent -> ingest sequence of its last event or report
        self._seen_seq = 0
        self.counts = defaultdict(int)  # skipped_malformed, unmapped, expected, duplicate, agent_cap, ...

    def boundary_id(self, agent_key: str, suffix: str) -> str:
        return f"stack.agent.{agent_key}.{suffix}"

    def limit_for(self, agent_id: str, reason: str) -> Optional[float]:
        """What the kernel should read before its next execution for this agent."""
        key, spec = _agent_key(agent_id), REASON_TO_LIMIT.get(reason)
        if key is None or spec is None or key not in self._agents:
            return None
        return float(self.gov.boundaries.get_boundary(self.boundary_id(key, spec[0])).current_limit)

    QUANTILE_POINTS = (0.50, 0.75, 0.90, 0.95, 0.99)

    def _touch(self, agent: str) -> None:
        self._seen_seq += 1
        self._last_seen[agent] = self._seen_seq

    def _evict_idle_agent(self) -> bool:
        """Make room for a new agent by forgetting the registered agent that has been quiet longest and whose
        limits were never tightened. A flood of throwaway ids then churns instead of locking later agents out
        for good (red team finding S3, docs/RED_TEAM.md). An agent whose limit was tightened is never evicted:
        its boundary and its evidence must survive. Returns False when every agent has been tightened."""
        candidates = [a for a in self._agents if not any(
            self.boundary_id(a, s) in self._tightened_boundaries for _r, (s, _f) in REASON_TO_LIMIT.items())]
        if not candidates:
            return False
        victim = min(candidates, key=lambda a: self._last_seen.get(a, -1))
        self._agents.discard(victim)
        for d in (self._tasks, self._slices, self._baseline, self._last_seen, self._medians, self._quantiles,
                  self._base_q, self._tallied, self._reviews):
            d.pop(victim, None)
        for d in (self._recent, self._stamps):
            for k in [k for k in d if k.startswith(victim + "|")]:
                d.pop(k, None)
        self._drifting.discard(victim)
        self.counts["agent_evicted"] += 1
        return True

    def observe_transactions(self, agent_id: str, n: int, completed_median_ns: Optional[float] = None,
                             completed_quantiles: Optional[list] = None, canary_tasks: int = 0,
                             canary_deadline_traps: int = 0) -> None:
        """Report that the agent ran n more transactions (traps or not), and optionally the median duration of the
        ones that completed. canary_tasks of the n ran on the ORIGINAL deadline (see TrapPolicy.validate_after) and
        canary_deadline_traps of those overran it. Call before ingesting their traps."""
        key = _agent_key(agent_id)
        if key is None or not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            return
        ok_int = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
        if not (ok_int(canary_tasks) and ok_int(canary_deadline_traps) and canary_tasks <= n
                and canary_deadline_traps <= canary_tasks):
            canary_tasks, canary_deadline_traps = 0, 0
        self._review_if_due(key)            # over completed slices only: this slice's traps are not in yet
        self._touch(key)
        self._tasks[key] += n
        self._slices[key].append([n, 0, canary_tasks, canary_deadline_traps])
        q = completed_quantiles
        if (isinstance(q, (list, tuple)) and len(q) == len(self.QUANTILE_POINTS)
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in q)
                and all(q[i] <= q[i + 1] for i in range(len(q) - 1))):
            qs = self._quantiles[key]
            qs.append([float(v) for v in q])
            if key not in self._base_q and len(qs) >= self.policy.baseline_reports:
                first = qs[: self.policy.baseline_reports]
                self._base_q[key] = [statistics.fmean(col) for col in zip(*first)]
            del qs[: max(0, len(qs) - 1000)]
        m = completed_median_ns
        if isinstance(m, (int, float)) and not isinstance(m, bool) and math.isfinite(m) and m > 0:
            reports = self._medians[key]
            reports.append(float(m))
            if key not in self._baseline and len(reports) >= self.policy.baseline_reports:
                self._baseline[key] = statistics.median(reports[: self.policy.baseline_reports])
            del reports[: max(0, len(reports) - 1000)]

    def _survival(self, key: str, x: float) -> Optional[float]:
        """Share of the agent's baseline (old) durations above x, from the frozen baseline quantiles."""
        qv = self._base_q.get(key)
        if qv is None:
            return None
        ps = [1 - p for p in self.QUANTILE_POINTS]            # survival at each quantile: .5 .25 .1 .05 .01
        if x <= qv[0]:
            return min(1.0, 1.0 - 0.5 * x / qv[0])
        for i in range(len(qv) - 1):
            if x <= qv[i + 1]:
                lo, hi = qv[i], qv[i + 1]
                t = 0.0 if hi <= lo else (x - lo) / (hi - lo)
                return math.exp((1 - t) * math.log(ps[i]) + t * math.log(ps[i + 1]))
        width = max(qv[-1] - qv[-2], 1e-9 * qv[-1])            # exponential tail beyond p99
        return ps[-1] * math.exp(-(x - qv[-1]) * math.log(ps[-2] / ps[-1]) / width)

    def _slowdown(self, key: str, observed_q: list, deadline_ns: float) -> Optional[float]:
        """The factor f whose model best reproduces the observed completed-duration quantiles: the baseline distribution
        scaled by f and TRUNCATED at the deadline (tasks the deadline killed never complete). Median / baseline median is
        biased low, and the truncated median alone stops identifying f once more than ~15% of tasks are killed, so all
        five quantiles are fitted (least squares on the logs, geometric grid from 1x to 20x)."""
        if key not in self._base_q or not observed_q:
            return None
        memo = (key, tuple(observed_q), float(deadline_ns))   # the baseline is frozen, so this is a pure function
        if memo in self._slow_cache:
            return self._slow_cache[memo]
        cdf = lambda x: 1.0 - self._survival(key, x)

        def inv(target):                                         # baseline quantile function by bisection
            lo, hi = 0.0, 4.0 * self._base_q[key][-1]
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if cdf(mid) < target else (lo, mid)
            return (lo + hi) / 2

        best, best_f = float("inf"), 1.0
        for i in range(121):
            f = 20.0 ** (i / 120.0)
            top = cdf(deadline_ns / f)
            if top <= 0:
                continue
            err = sum((math.log(max(f * inv(p * top), 1.0)) - math.log(o)) ** 2
                      for p, o in zip(self.QUANTILE_POINTS, observed_q))
            if err < best:
                best, best_f = err, f
        if len(self._slow_cache) >= 4096:
            self._slow_cache.clear()
        self._slow_cache[memo] = best_f
        return best_f

    def excess_released(self, agent_key: str, deadline_ns: float) -> bool:
        """True if the observed deadline-trap share is well above what the slowdown alone explains."""
        base, reports = self._baseline.get(agent_key), self._medians.get(agent_key)
        sl = list(self._slices.get(agent_key, ()))[-self.policy.excess_window:]
        if base is None or not reports or not sl or agent_key not in self._base_q:
            return False
        f = self._slowdown(agent_key, self._quantiles[agent_key][-1] if self._quantiles.get(agent_key) else [], deadline_ns)
        if f is None:
            return False
        tasks = sum(w[0] for w in sl)
        if tasks <= 0 or f <= 1:
            return False
        explained = self._survival(agent_key, deadline_ns / f)
        observed = sum(w[1] for w in sl) / tasks
        return observed > explained + max(self.policy.excess_abs, self.policy.excess_rel * explained)

    def drifting(self, agent_key: str) -> bool:
        """True if the agent's recent completed tasks are slower than its frozen baseline by drift_factor."""
        base, reports = self._baseline.get(agent_key), self._medians.get(agent_key)
        if self.policy.drift_factor is None or base is None or not reports:
            return False
        recent = statistics.median(reports[-self.policy.drift_recent_reports:])
        return recent > base * self.policy.drift_factor

    def _report_drift(self, agent: str, suffix: str, reason: str) -> None:
        """Once per episode: record it in the audit log, and if the limit was already tightened, ask a person to
        restore it. Nothing is changed here."""
        bid = self.boundary_id(agent, suffix)
        base, recent = self._baseline[agent], statistics.median(self._medians[agent][-self.policy.drift_recent_reports:])
        info = {"kind": "drift_held", "source": self.source, "boundary_id": bid, "trap_reason": reason,
                "baseline_median_ns": base, "recent_median_ns": recent}
        self.gov.audit.append("signal", info)
        current = float(self.gov.boundaries.get_boundary(bid).current_limit)
        if current < float(self.defaults[suffix]):
            try:
                self.gov.request_loosening(
                    bid, requested_by=self.source, credential=self.credential, new_limit=float(self.defaults[suffix]),
                    reason="completed tasks are slower than this agent's baseline: looks like a regression, "
                           "not a runaway", evidence=info, idempotency_key=f"drift-{agent}-{suffix}-{int(recent)}")
            except (ValueError, KeyError):
                self.counts["rejected_input"] += 1

    def deadline_floor(self, agent_key: str) -> Optional[float]:
        """The lowest deadline the cost budget allows for this agent, or None if no budget is set or too little is known.

        Uses the agent's own per-slice completed-duration quantile at the budget's point: the larger of its mean over all
        retained slices and its mean over the last cost_budget_recent_reports slices (so a slowdown raises the floor
        quickly and a recovery lowers it slowly). Completed tasks exclude the ones already killed, so the true share of all
        tasks above this floor is at most the budget."""
        if self.policy.cost_budget is None:
            return None
        qs = self._quantiles.get(agent_key)
        if not qs or len(qs) < self.policy.cost_budget_min_reports:
            return None
        i = COST_BUDGET_POINT[self.policy.cost_budget]
        col = [q[i] for q in qs]
        return max(statistics.fmean(col), statistics.fmean(col[-self.policy.cost_budget_recent_reports:]))

    # ---------- class-conditioned deadlines (round 23) ----------

    def _class_boundary(self, agent: str, ckey: str) -> str:
        return f"stack.agent.{agent}.class.{ckey}.deadline_ns"

    def _class_keys(self, agent_id: Any, tool_class: Any):
        agent, ckey = _agent_key(agent_id), _agent_key(tool_class)
        return (agent, ckey) if agent is not None and ckey is not None else (None, None)

    def observe_class_durations(self, agent_id: str, tool_class: str, completed_ns: Iterable[float]) -> int:
        """Record durations of calls of this class that COMPLETED (were not killed). Returns how many were kept. Does not
        change any limit; refresh_class_deadlines does."""
        if not self.policy.class_deadlines:
            return 0
        agent, ckey = self._class_keys(agent_id, tool_class)
        if agent is None:
            self.counts["class_malformed"] += 1
            return 0
        key = (agent, ckey)
        if key not in self._class_obs:
            if sum(1 for a, _c in self._class_obs if a == agent) >= self.policy.class_max_per_agent:
                self.counts["class_cap"] += 1
                return 0
            if agent not in self._agents and len(self._agents) >= self.max_agents and not self._evict_idle_agent():
                self.counts["agent_cap"] += 1
                return 0
            self._class_obs[key] = deque(maxlen=self.policy.class_history)
        kept = 0
        for x in completed_ns:
            if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0:
                self._class_obs[key].append(float(x)); kept += 1
        return kept

    def class_deadline(self, agent_id: str, tool_class: str) -> float:
        """What the kernel should enforce for this agent's calls of this class: the governed class deadline, or the configured
        deadline_ns if the class has none yet."""
        agent, ckey = self._class_keys(agent_id, tool_class)
        if agent is not None and (agent, ckey) in self._class_obs:
            bid = self._class_boundary(agent, ckey)
            try:
                return float(self.gov.boundaries.get_boundary(bid).current_limit)
            except (KeyError, ValueError):
                pass
        return float(self.defaults["deadline_ns"])

    def refresh_class_deadlines(self, agent_id: str) -> Dict[str, float]:
        """Re-read this agent's class histories and tighten any class whose target is below its current deadline. Call at
        the start of each reporting slice. Returns {class key: new deadline} for classes that changed. Never loosens."""
        changed: Dict[str, float] = {}
        if not self.policy.class_deadlines:
            return changed
        agent = _agent_key(agent_id)
        if agent is None:
            return changed
        D, p = float(self.defaults["deadline_ns"]), self.policy
        for (a, ckey), obs in list(self._class_obs.items()):
            if a != agent:
                continue
            bid = self._class_boundary(agent, ckey)
            self.counts["class_decisions"] += 1
            if len(obs) < p.class_min_completions:
                self._class_audit("class_deadline_unknown", bid, {"completed": len(obs), "needed": p.class_min_completions})
                continue
            target = min(D, max(p.class_floor * D, p.class_margin * _quantile(obs, 0.99)))
            if bid not in self._class_ready:        # once: ensure_boundary logs "configured_limit_ignored" on every later call
                self.gov.ensure_boundary(bid, "agent_limit", D)
                self._class_ready.add(bid)
            current = float(self.gov.boundaries.get_boundary(bid).current_limit)
            if not target < current:
                self._class_audit("class_deadline_held", bid, {"completed": len(obs), "target_ns": target, "current_ns": current})
                continue
            self._agents.add(agent); self._touch(agent)
            try:
                reply = self.gov.propose_tightening(
                    bid, source=self.source, credential=self.credential, new_limit=target,
                    reason=f"class deadline {p.class_margin} x p99 of {len(obs)} completed calls",
                    evidence={"agent": agent, "class": ckey, "completed": len(obs), "target_ns": target},
                    idempotency_key=f"class-{bid}-{int(target)}")
            except (ValueError, KeyError):
                self.counts["rejected_input"] += 1
                continue
            self.counts["class_reply_" + str(reply.get("status"))] += 1
            if reply.get("status") not in ("applied", "applied_clamped"):
                # The governor logs a breaker hold once per hold episode, not per request, so the bridge records each
                # refused request itself: every class decision is then in the log.
                self._class_audit("class_tighten_not_applied", bid, {"status": reply.get("status"), "reason": reply.get("reason"),
                                                                       "target_ns": target, "current_ns": current})
            if reply.get("status") in ("applied", "applied_clamped"):
                self._tightened_boundaries.add(bid)
                self.counts["class_tightened"] += 1
                self.counts["class_audited"] += 1
                changed[ckey] = float(reply.get("limit_after", target))
        return changed

    def _class_audit(self, kind: str, bid: str, info: dict) -> None:
        """Record a class decision that changed nothing. If the audit write fails nothing else changes and the failure is counted."""
        try:
            self.gov.audit.append(kind, {"boundary_id": bid, "source": self.source, **info})
            self.counts["class_audited"] += 1
        except Exception:
            self.counts["audit_write_failed"] += 1

    def request_class_loosening(self, agent_id: str, tool_class: str, new_limit_ns: float, reason: str = "") -> dict:
        """Ask a named operator to raise one class deadline. Nothing changes until an operator approves it."""
        agent, ckey = self._class_keys(agent_id, tool_class)
        if agent is None or (agent, ckey) not in self._class_obs:
            return {"status": "rejected", "reason": "unknown agent or class"}
        bid = self._class_boundary(agent, ckey)
        self.gov.ensure_boundary(bid, "agent_limit", float(self.defaults["deadline_ns"]))
        return self.gov.request_loosening(bid, requested_by=self.source, credential=self.credential,
                                          new_limit=float(new_limit_ns), reason=reason or "class deadline too tight")

    def _min_rate_for(self, agent_id: Any) -> float:
        by_class = self.policy.min_rate_by_class
        if by_class and self.agent_class is not None:
            try:
                cls = self.agent_class(agent_id)
            except Exception:
                cls = None
            if isinstance(cls, str) and cls in by_class:
                return float(by_class[cls])
        return float(self.policy.min_rate)

    def canary_plan(self, agent_id: str) -> Optional[dict]:
        """What the kernel should do for validation: if the agent's deadline is tightened and validation is on,
        run `share` of its tasks on `deadline_ns` (the configured original). None when nothing to do."""
        key = _agent_key(agent_id)
        if self.policy.validate_after is None or key is None or key not in self._agents:
            return None
        current = float(self.gov.boundaries.get_boundary(self.boundary_id(key, "deadline_ns")).current_limit)
        original = float(self.defaults["deadline_ns"])
        if current >= original:
            return None
        plan = {"share": self.policy.canary_share, "deadline_ns": original}
        if self.policy.canary_key:
            plan["key"] = self.policy.canary_key
        return plan

    def _schedule_review(self, agent: str, suffix: str, reply: dict) -> None:
        """Each applied tightening (re)starts the review episode: the first verdict comes validate_after slices after
        the latest step. Round 7 of docs/TRACE_TEST_PLAN.md tried measuring from the FIRST step instead; it changed
        nothing for the agents it was meant for (their three steps land in one slice) and its check failed, so it
        was reverted."""
        if self.policy.validate_after is None or suffix != "deadline_ns":
            return
        self._reviews[agent] = {"at_slice": len(self._slices[agent]), "since": len(self._slices[agent]),
                                "version": reply.get("version"), "limit": reply.get("limit_after")}

    def _review_if_due(self, agent: str) -> None:
        """Validation layer: judge the agent's tightened deadline against the canary, every validate_after slices."""
        rv = self._reviews.get(agent)
        if rv is None or len(self._slices[agent]) - rv["at_slice"] < self.policy.validate_after:
            return
        bid = self.boundary_id(agent, "deadline_ns")
        d1 = float(self.gov.boundaries.get_boundary(bid).current_limit)
        d0 = float(self.defaults["deadline_ns"])
        if d1 >= d0:
            self._reviews.pop(agent, None)
            return
        all_slices = list(self._slices[agent])
        window = all_slices[max(rv["since"], len(all_slices) - self.policy.review_window):]
        n_all = sum(w[0] for w in window); traps_all = sum(w[1] for w in window)
        c_n = sum(w[2] for w in window); c_traps = sum(w[3] for w in window)
        m_n, m_traps = n_all - c_n, traps_all - c_traps
        rv["at_slice"] = len(all_slices)                        # next review in validate_after slices
        if c_n < self.policy.min_canary_tasks or m_n <= 0:
            verdict, numbers = "UNKNOWN", {"canary_tasks": c_n, "main_tasks": m_n}
        else:
            z = self.policy.review_z
            main_share = min(1.0, max(0.0, m_traps / m_n)); canary_share = min(1.0, max(0.0, c_traps / c_n))
            se_m = math.sqrt(main_share * (1 - main_share) / m_n)
            se_c = math.sqrt(canary_share * (1 - canary_share) / c_n)
            se_d = math.sqrt(se_m ** 2 + se_c ** 2)
            delta = max(0.0, main_share - canary_share)
            d_lo = max(0.0, main_share - canary_share - z * se_d)
            d_hi = max(0.0, main_share - canary_share + z * se_d)
            c_hi = max(min(1.0, canary_share + z * se_c), 3.0 / c_n)   # rule of three when nothing was seen
            c_lo = max(0.0, canary_share - z * se_c)
            cost, benefit_max = delta * d1, canary_share * (d0 - d1)
            if d_lo * d1 > c_hi * (d0 - d1):
                verdict = "DEGRADED"
            elif c_lo > 0 and d_hi * d1 < self.policy.improved_ratio * c_lo * (d0 - d1):
                verdict = "IMPROVED"
            else:
                verdict = "UNCHANGED"
            numbers = {"main_share": main_share, "canary_share": canary_share, "delta": delta,
                       "cost_ns_per_task": cost, "benefit_max_ns_per_task": benefit_max,
                       "cost_lower_ns": d_lo * d1, "cost_upper_ns": d_hi * d1,
                       "benefit_lower_ns": c_lo * (d0 - d1), "benefit_upper_ns": c_hi * (d0 - d1),
                       "main_tasks": m_n, "canary_tasks": c_n, "slices": len(window)}
        record = {"kind": "tightening_reviewed", "source": self.source, "boundary_id": bid, "agent": agent,
                  "verdict": verdict, "deadline_ns": d1, "original_deadline_ns": d0, "version": rv.get("version"),
                  "slice": len(self._slices[agent]), **numbers}
        self.verdicts.append(record)
        self.counts[f"review_{verdict.lower()}"] += 1
        self.gov.audit.append("signal", record)
        try:
            self.gov.validations.record_validation(
                proposal_id=f"trap-review-{agent}-{rv.get('version')}", boundary_id=bid,
                expected_condition="the tightened deadline cuts hung tasks short without killing tasks that complete under the original",
                observed_condition=f"main trap share {numbers.get('main_share')}, canary trap share {numbers.get('canary_share')}",
                outcome=_OUTCOME[verdict], notes=numbers)
        except Exception:
            self.counts["review_record_failed"] += 1
        if verdict == "DEGRADED" and bid not in self._restore_pending:
            try:
                r = self.gov.request_loosening(
                    bid, requested_by=self.source, credential=self.credential, new_limit=d0,
                    reason="validation: the tightened deadline kills tasks that complete under the original and "
                           "burns more time than it saves; a person should restore it",
                    evidence=record, idempotency_key=f"review-{agent}-{rv.get('version')}-{record['slice']}")
                if r.get("status") == "pending_review":
                    self._restore_pending.add(bid)
                    self.counts["restore_requested"] += 1
            except (ValueError, KeyError):
                self.counts["rejected_input"] += 1

    def ingest(self, events: Iterable[dict]) -> List[dict]:
        """Process trap events in order. Never raises on a bad event; returns the governor's replies."""
        replies: List[dict] = []
        for ev in events:
            r = self._one(ev)
            if r is not None:
                replies.append(r)
        return replies

    def _one(self, ev: Any) -> Optional[dict]:
        if not isinstance(ev, dict):
            self.counts["skipped_malformed"] += 1
            return None
        agent, reason, trap_id = _agent_key(ev.get("agent_id")), ev.get("trap_reason"), ev.get("trap_id")
        if agent is None or not isinstance(reason, str) or not isinstance(trap_id, str) or not trap_id:
            self.counts["skipped_malformed"] += 1
            return None
        if ev.get("trap_outcome") not in self.policy.count_outcomes:
            self.counts["skipped_outcome"] += 1
            return None
        if reason not in REASON_TO_LIMIT:
            self.counts["unmapped"] += 1
            return None
        if agent in self._agents:
            self._touch(agent)
        if self.policy.reasons is not None and reason not in self.policy.reasons:
            self.counts["reason_not_enforced"] += 1
            return None
        if reason == "deadline_exceeded" and self._slices[agent] and trap_id not in self._tallied[agent]:
            # The slice tally feeds the validation review and must count EVERY deadline trap of this slice, whether
            # or not the policy below acts on it (expected load, a review hold, a drift hold). Round 6 found the
            # tally skipping held traps while the canary count did not, which made the main share negative.
            self._tallied[agent].append(trap_id)
            self._slices[agent][-1][1] += 1
        if self.is_expected is not None and self.is_expected(ev):
            self.counts["expected"] += 1
            return None
        suffix, ctx_field = REASON_TO_LIMIT[reason]
        if agent in self._agents and self.boundary_id(agent, suffix) in self._restore_pending:
            self.counts["review_hold"] += 1       # a DEGRADED verdict is waiting for a person: no more tightening
            return None
        if agent not in self._agents:
            if len(self._agents) >= self.max_agents and not self._evict_idle_agent():
                self.counts["agent_cap"] += 1
                return None
            self._agents.add(agent)
            self._touch(agent)
            for _r, (s, f) in REASON_TO_LIMIT.items():
                ctx = ev.get("context") if isinstance(ev.get("context"), dict) else {}
                configured = ctx.get(f)
                start = float(configured) if isinstance(configured, (int, float)) and not isinstance(configured, bool) \
                    and configured > 0 else float(self.defaults[s])
                self.gov.ensure_boundary(self.boundary_id(agent, s), "agent_limit", start)
        window = self._recent[f"{agent}|{reason}"]
        if trap_id in window:
            self.counts["duplicate"] += 1
            return None
        window.append(trap_id)
        stamps = self._stamps[f"{agent}|{reason}"]
        stamps.append(self._tasks[agent])
        if len(window) < self.policy.min_traps:     # the window already holds only the last window_events
            return None
        if self.policy.min_rate is not None:
            total = self._tasks[agent]
            if total <= 0:
                self.counts["no_activity"] += 1
                return None
            span = min(total, self.policy.rate_window_tasks)
            recent = sum(1 for st in stamps if st > total - span)
            if recent < self.policy.min_traps or recent / span < self._min_rate_for(ev.get("agent_id")):
                self.counts["below_rate"] += 1
                return None
        if self.policy.drift_factor is not None:
            drifting = self.drifting(agent)
            if drifting and self.policy.drift_excess and self.excess_released(
                    agent, float(self.gov.boundaries.get_boundary(self.boundary_id(agent, suffix)).current_limit)
                    if suffix == "deadline_ns" else float(self.defaults["deadline_ns"])):
                self.counts["drift_released"] += 1
                self._drifting.discard(agent)
            elif drifting:
                self.counts["drift_held"] += 1
                if agent not in self._drifting:
                    self._drifting.add(agent)
                    self._report_drift(agent, suffix, reason)
                return None
            elif agent in self._drifting:
                # Completions have recovered while the hold stood. The traps gathered during the slowdown were
                # explained by it and are spent: without this, the first natural trap after a recovery tightened
                # on the slow period's evidence (found in the multi-seed replays, docs/TRACE_TEST_PLAN.md).
                # An agent released by the excess rule is no longer in _drifting, so its evidence is kept.
                self.counts["drift_recovered"] += 1
                self._drifting.discard(agent)
                window.clear(); stamps.clear()
                window.append(trap_id); stamps.append(self._tasks[agent])
                return None
        evidence = {k: ev.get(k) for k in ("trap_id", "agent_id", "boundary_layer", "trap_reason", "trap_outcome",
                                            "tokens_deficit", "nanoseconds_since_deadline", "memory_requested_bytes")}
        evidence["traps_in_window"] = len(window)
        factor = self.policy.factor
        if self.policy.cost_budget is not None and suffix == "deadline_ns":
            floor = self.deadline_floor(agent)
            if floor is None and self.policy.cost_budget_new_identity_ratio is not None:
                floor = self.policy.cost_budget_new_identity_ratio * float(self.defaults["deadline_ns"])
                self.counts["budget_provisional"] += 1
            if floor is None:
                self.counts["budget_unknown"] += 1
                return None
            current = float(self.gov.boundaries.get_boundary(self.boundary_id(agent, suffix)).current_limit)
            if floor >= current:
                self.counts["budget_held"] += 1
                window.clear(); stamps.clear()
                return None
            factor = max(factor, floor / current)       # tighten by the policy step, but never below the floor
            if factor > self.policy.factor:
                self.counts["budget_clamped"] += 1
        try:
            reply = self.gov.propose_tightening(
                self.boundary_id(agent, suffix), source=self.source, credential=self.credential,
                factor=factor, reason=f"{len(window)} {reason} traps for {agent}",
                evidence=evidence, idempotency_key=f"trap-{trap_id}")
        except (ValueError, KeyError):
            self.counts["rejected_input"] += 1
            return None
        window.clear()  # the next tightening needs a fresh pattern
        stamps.clear()
        if reply.get("status") in ("applied", "applied_clamped"):
            self._tightened_boundaries.add(self.boundary_id(agent, suffix))
            self._schedule_review(agent, suffix, reply)
        return reply
