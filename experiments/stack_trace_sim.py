#!/usr/bin/env python3
"""Replay a trace CSV through a Python model of the kernel's limit checks and the real trap loop.

`stack_trace_replay.py` runs every slice through the Rust kernel (`governed_workload`). That is the reference.
This file replaces only the kernel with a few lines of Python (deadline, token and memory checks; per-slice
summary) so that traces can be replayed without the Rust toolchain, many seeds can be run, and policy
parameters can be swept. Everything else is the same code: TrapBridge, TrapPolicy, the Governor, the audit log.

The model is checked against the kernel's committed results (`stack_trace_replay_results*.json`) by
`tests/test_trace_sim.py`: identical static totals and identical tightening decisions on all three traces.
Where the two could differ (which check fires first, how quantiles are computed), the model follows the
committed numbers, not a reading of the Rust source. It is a model of the kernel, not the kernel.

usage: python experiments/stack_trace_sim.py [--csv testdata/traces/stack_trace_v1.csv] [--policies a,b]
       [--slice 250] [--out experiments/stack_trace_sim_results.json] [--quiet]
"""
import math
import argparse, csv, json, os, sys, tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.stack_traps import TrapBridge, TrapPolicy, REASON_TO_LIMIT   # noqa: E402
from src.governance.governor import Governor                                     # noqa: E402

POLICIES = {
    "default": lambda: TrapPolicy(),
    "rate": lambda: TrapPolicy(min_rate=0.01),
    "rate_drift": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1),
    "rate_drift_excess": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True),
    "recommended": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True,
                                      reasons=("deadline_exceeded",)),
    "validated": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True,
                                    reasons=("deadline_exceeded",), validate_after=4),
}
# Ninth round: the two-way baselines. These need defaults, which replay() supplies through the factory's argument.
POLICIES["aimd"] = lambda defaults=None: AIMDController(defaults or {"deadline_ns": 100_000_000}, increase=0.02)
POLICIES["aimd_fast"] = lambda defaults=None: AIMDController(defaults or {"deadline_ns": 100_000_000}, increase=0.10)
# Round 9b: the two-way limiter with the ratchet's breaker cap as its floor, to separate the cap from the direction.
POLICIES["aimd_capped"] = lambda defaults=None: AIMDController(defaults or {"deadline_ns": 100_000_000}, increase=0.02, floor=0.729)
# Round 11c: the breaker cap as a dial on the recommended policy.
def _recommended_cap(k):
    def make(defaults=None):
        pol = TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True, reasons=("deadline_exceeded",))
        pol.governor_kwargs = {"max_auto_tightenings": k}
        return pol
    return make
for _k in (1, 2, 3, 5, 8):
    POLICIES[f"recommended_cap{_k}"] = _recommended_cap(_k)
# Round 10: the cap plus the drift hold; and the increase-rate sweep for the pulse formula.
for _k in (8, 16):
    POLICIES[f"twoway_h{_k}"] = (lambda k: lambda defaults=None: HysteresisController(
        defaults or {"deadline_ns": 100_000_000}, calm_run=k, steps_per=5, increase=0.02, floor=0.729, drift_factor=1.5))(_k)
for _k in (8, 16):
    POLICIES[f"twoway_bridge_h{_k}"] = (lambda k: lambda defaults=None: BridgeTwoWay(
        defaults or {"deadline_ns": 100_000_000, "tokens_capacity": 4000, "memory_capacity_bytes": 512 * 1024 * 1024},
        TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True, reasons=("deadline_exceeded",)),
        calm_run=k))(_k)
# Round 14: the ratchet with the two-way baseline's per-slice trigger (3 traps in the last 250 tasks at 1%), so the
# phase-7 catch of round 12 can be attributed to the trigger or to the direction (docs/TRACE_TEST_PLAN.md, round 14).
POLICIES["recommended_slice"] = lambda: TrapPolicy(min_rate=0.01, min_traps=3, rate_window_tasks=250, drift_factor=1.5,
                                                   drift_recent_reports=1, drift_excess=True, reasons=("deadline_exceeded",))
POLICIES["aimd_capped_drift"] = lambda defaults=None: AIMDController(defaults or {"deadline_ns": 100_000_000}, increase=0.02,
                                                                    floor=0.729, drift_factor=1.5)
for _inc in (0.01, 0.02, 0.05, 0.10):
    POLICIES[f"aimd_capped_i{int(_inc * 100)}"] = (lambda inc: lambda defaults=None: AIMDController(
        defaults or {"deadline_ns": 100_000_000}, increase=inc, floor=0.729))(_inc)
# Sixth round: canary share and per-class thresholds (docs/TRACE_TEST_PLAN.md). Class = the trace's team column.
CLASS_RATES = {"core": 0.02, "ext": 0.005}
for _share in (0.10, 0.20, 0.30):
    POLICIES[f"keyed_c{int(_share * 100)}"] = (lambda sh: lambda: TrapPolicy(
        min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True, reasons=("deadline_exceeded",),
        validate_after=4, canary_share=sh, canary_key="k-7f3a"))(_share)
    POLICIES[f"validated_c{int(_share * 100)}"] = (lambda sh: lambda: TrapPolicy(
        min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True, reasons=("deadline_exceeded",),
        validate_after=4, canary_share=sh))(_share)
    POLICIES[f"class_c{int(_share * 100)}"] = (lambda sh: lambda: TrapPolicy(
        min_rate=0.01, min_rate_by_class=dict(CLASS_RATES), drift_factor=1.5, drift_recent_reports=1, drift_excess=True,
        reasons=("deadline_exceeded",), validate_after=4, canary_share=sh))(_share)
SUFFIX = ("deadline_ns", "tokens_capacity", "memory_capacity_bytes")


class AIMDController:
    """The two-way baseline (ninth round): lower the deadline on trouble, raise it again when calm. Same trigger, step
    and floor as the ratchet's rate rule; no breaker, no human, no drift rule. It is NOT a TrapBridge and does not go
    through the governor, because the governor cannot raise a limit, which is the whole difference under test."""

    def __init__(self, defaults, increase=0.02, calm_share=0.005, min_rate=0.01, min_traps=3, factor=0.9, floor=0.20,
                 drift_factor=None, baseline_slices=5):
        """Reacts to the CURRENT slice, as AIMD limiters do: decrease when this slice's traps are at least min_traps
        and at least min_rate of its tasks; increase when its share is under calm_share. (A first version reused the
        ratchet's 2,000-task trigger window and could never increase while a pulse was still inside the window; it
        was replaced before any result was scored. docs/TRACE_TEST_PLAN.md, round 9.)"""
        self.defaults = dict(defaults); self.increase, self.calm_share = increase, calm_share
        self.min_rate, self.min_traps, self.factor, self.floor = min_rate, min_traps, factor, floor
        self.limit = {}; self.counts = {"decrease": 0, "increase": 0}; self.verdicts = []
        self.min_limit = {}
        # Round 10a: the ratchet's drift hold, transplanted. Baseline = median of the first baseline_slices completed
        # medians; no decrease while the latest median exceeds drift_factor x baseline. No excess release.
        self.drift_factor, self.baseline_slices = drift_factor, baseline_slices
        self.medians = {}; self.baseline = {}

    def canary_plan(self, agent):
        return None

    def limit_for(self, agent, reason):
        if reason != "deadline_exceeded":
            return None
        return self.limit.get(agent)

    def observe_transactions(self, agent, n, **_):
        pass

    def observe_slice(self, agent, summary):
        orig = float(self.defaults["deadline_ns"])
        cur = self.limit.get(agent, orig)
        traps = summary["deadline_traps"]
        share = traps / max(1, summary["tasks"])
        drifting = False
        if self.drift_factor is not None:
            m = summary.get("completed_median_ns")
            if m:
                ms = self.medians.setdefault(agent, []); ms.append(float(m))
                if agent not in self.baseline and len(ms) >= self.baseline_slices:
                    srt = sorted(ms[: self.baseline_slices]); self.baseline[agent] = srt[len(srt) // 2]
                if agent in self.baseline and ms[-1] > self.drift_factor * self.baseline[agent]:
                    drifting = True; self.counts["drift_held"] = self.counts.get("drift_held", 0) + 1
        if drifting:
            pass
        elif traps >= self.min_traps and share >= self.min_rate:
            cur = max(orig * self.floor, cur * self.factor); self.counts["decrease"] += 1
        elif share < self.calm_share and cur < orig:
            cur = min(orig, cur + self.increase * orig); self.counts["increase"] += 1
        self.limit[agent] = cur
        self.min_limit[agent] = min(self.min_limit.get(agent, orig), cur)

    def ingest(self, traps):
        return []


class HysteresisController(AIMDController):
    """A two-way limiter with the three things the red team gave the baseline that the author's never had
    (docs/RED_TEAM.md B2): an increase needs calm_run consecutive calm slices, not one; one decrease step per
    steps_per traps in a slice (at most max_steps), which is the ratchet's own step rule; and declared expected-load
    traps are not counted (the ratchet's is_expected hook). Everything else is AIMDController unchanged."""

    def __init__(self, defaults, calm_run=16, steps_per=5, max_steps=3, exclude_expected=True, **kw):
        super().__init__(defaults, **kw)
        self.calm_run, self.steps_per, self.max_steps, self.exclude_expected = calm_run, steps_per, max_steps, exclude_expected
        self.calm = {}

    def observe_slice(self, agent, summary):
        orig = float(self.defaults["deadline_ns"])
        cur = self.limit.get(agent, orig)
        traps = summary["deadline_traps"]
        if self.exclude_expected:
            traps = max(0, traps - summary.get("expected_trapped", 0))
        share = traps / max(1, summary["tasks"])
        drifting = False
        if self.drift_factor is not None:
            m = summary.get("completed_median_ns")
            if m:
                ms = self.medians.setdefault(agent, []); ms.append(float(m))
                if agent not in self.baseline and len(ms) >= self.baseline_slices:
                    srt = sorted(ms[: self.baseline_slices]); self.baseline[agent] = srt[len(srt) // 2]
                if agent in self.baseline and ms[-1] > self.drift_factor * self.baseline[agent]:
                    drifting = True; self.counts["drift_held"] = self.counts.get("drift_held", 0) + 1
        self.calm[agent] = self.calm.get(agent, 0) + 1 if share < self.calm_share else 0
        if drifting:
            pass
        elif traps >= self.min_traps and share >= self.min_rate:
            steps = 1 if not self.steps_per else max(1, min(self.max_steps, traps // self.steps_per))
            for _ in range(steps):
                cur = max(orig * self.floor, cur * self.factor)
            self.counts["decrease"] += steps
        elif share < self.calm_share and cur < orig and self.calm[agent] >= self.calm_run:
            cur = min(orig, cur + self.increase * orig); self.counts["increase"] += 1
        self.limit[agent] = cur
        self.min_limit[agent] = min(self.min_limit.get(agent, orig), cur)


class _LimitBook:
    """Stands in for the Governor under BridgeTwoWay: it holds limits, applies the bridge's tightenings against a
    floor instead of a breaker, lets the controller raise them, and shows nothing to a person. The TrapBridge
    calls ensure_boundary, boundaries.get_boundary, propose_tightening, request_loosening and audit.append."""

    class _B:
        __slots__ = ("current_limit",)

        def __init__(self, v):
            self.current_limit = v

    def __init__(self, floor):
        self.limits = {}; self.floors = {}; self.floor = floor
        self.boundaries = self
        self.audit = self; self.validations = self
        self.counts = {"applied": 0, "floor_held": 0, "loosen_requested": 0}

    def ensure_boundary(self, bid, _resource, start):
        if bid not in self.limits:
            self.limits[bid] = float(start); self.floors[bid] = float(start) * self.floor

    def get_boundary(self, bid):
        return self._B(self.limits[bid])

    def propose_tightening(self, bid, factor=0.9, **_kw):
        cur = self.limits[bid]; new = max(self.floors[bid], cur * factor)
        if new >= cur:
            self.counts["floor_held"] += 1
            return {"status": "held", "reason": "floor"}
        self.limits[bid] = new; self.counts["applied"] += 1
        return {"status": "applied", "new_limit": new}

    def request_loosening(self, _bid, **_kw):
        self.counts["loosen_requested"] += 1   # a person is not asked: the controller recovers on its own
        return {"status": "recorded"}

    def append(self, _kind, _payload):
        return None

    def record_validation(self, *_a, **_k):
        return None


class BridgeTwoWay(AIMDController):
    """Round 13: a two-way limiter that carries the ratchet's ENTIRE interpretation layer, because it is the ratchet's
    own TrapBridge (rate rule over the filling window, drift hold with the truncation-aware slowdown fit, excess
    release, recovery clearing, expected-load hook) driving a limit book instead of the governor. Differences from
    the ratchet: the cap is a floor at `floor` x original rather than three steps and a hold; nobody is asked
    anything; and after `calm_run` consecutive calm slices the limit comes back up by `increase` x original per slice.
    If this matches the ratchet on the cells the plain two-way lost (inflate-first attackers, 75 ms regressions), those
    cells were interpretation, not direction."""

    def __init__(self, defaults, policy, calm_run=16, increase=0.02, floor=0.729, calm_share=0.005):
        super().__init__(defaults, increase=increase, calm_share=calm_share, floor=floor)
        self.book = _LimitBook(floor)
        self.bridge = TrapBridge(self.book, policy=policy, defaults=defaults,
                                 is_expected=lambda ev: bool(ev.get("expected_load")))
        self.calm_run, self.calm = calm_run, {}

    @property
    def counts(self):
        return {**self.bridge.counts, **self.book.counts, "increase": self._increases}

    @counts.setter
    def counts(self, v):
        self._increases = 0

    def _bid(self, agent):
        from integrations.stack_traps import _agent_key
        return self.bridge.boundary_id(_agent_key(agent), "deadline_ns")

    def observe_transactions(self, agent, n, **kw):
        self.bridge.observe_transactions(agent, n, **kw)

    def ingest(self, traps):
        return self.bridge.ingest(traps)

    def _sync(self, agent):
        orig = float(self.defaults["deadline_ns"]); bid = self._bid(agent)
        if bid in self.book.limits:
            cur = self.book.limits[bid]
            self.limit[agent] = cur
            self.min_limit[agent] = min(self.min_limit.get(agent, orig), cur)

    def observe_slice(self, agent, summary):
        orig = float(self.defaults["deadline_ns"]); bid = self._bid(agent)
        self._sync(agent)
        traps = max(0, summary["deadline_traps"] - summary.get("expected_trapped", 0))
        share = traps / max(1, summary["tasks"])
        self.calm[agent] = self.calm.get(agent, 0) + 1 if share < self.calm_share else 0
        if bid in self.book.limits and self.book.limits[bid] < orig and self.calm[agent] >= self.calm_run:
            self.book.limits[bid] = min(orig, self.book.limits[bid] + self.increase * orig)
            self._increases += 1
            self._sync(agent)


def is_controller(obj):
    return isinstance(obj, AIMDController)
LIMIT_TO_REASON = {s: r for r, (s, _f) in REASON_TO_LIMIT.items()}
QUANTILE_POINTS = (50, 75, 90, 95, 99)


def load_trace(csv_path):
    """rows grouped by agent, in seq order, as numpy columns."""
    cols = defaultdict(lambda: defaultdict(list))
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            c = cols[row["agent_id"]]
            c["task_id"].append(row["task_id"]); c["seq"].append(int(row["seq"]))
            c["duration_ns"].append(int(row["duration_ns"])); c["tokens"].append(int(row["tokens_requested"]))
            c["memory"].append(int(row["memory_requested_bytes"])); c["expected"].append(int(row["expected_load"]))
            c["is_bad"].append(int(row["is_bad"]))
    out = {}
    for agent, c in cols.items():
        order = np.argsort(np.array(c["seq"]))
        out[agent] = {k: (np.array(v, dtype=object) if k == "task_id" else np.array(v, dtype=np.int64))[order]
                      for k, v in c.items()}
    return out


def fnv1a64(data: bytes) -> int:
    h = 0xcbf29ce484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def canary_mask(seq, every, agent, key=None):
    """Which tasks run on the original deadline. Without a key: every `every`th task by seq (predictable, T16). With
    a key: tasks whose FNV-1a 64 hash of "key|agent|seq" is a multiple of `every` (the Rust example does the same)."""
    if key is None:
        return (seq % every) == 0
    return np.array([fnv1a64(f"{key}|{agent}|{int(q)}".encode("utf-8")) % every == 0 for q in seq], dtype=bool)


def run_slice(agent, rows, start, stop, limits, canary=None):
    """The kernel's job for one slice: check each task against the limits it read at the start of the slice.

    Deadline first (a task that overruns is killed and burns exactly the deadline), then tokens, then memory
    (a task refused for tokens or memory still burns its own duration in the committed kernel numbers).
    Expected-load tasks still trap, but are not counted as legitimate failures. Completed-duration statistics
    cover the tasks that no check trapped.

    canary: {"share": s, "deadline_ns": d0} keeps every (1/s)th task (by seq) on deadline d0 instead of the
    tightened one, for the validation layer (TrapBridge.canary_plan). Not in the Rust kernel example.
    """
    dl, tc, mc = limits["deadline_ns"], limits["tokens_capacity"], limits["memory_capacity_bytes"]
    dur = rows["duration_ns"][start:stop]; tok = rows["tokens"][start:stop]; mem = rows["memory"][start:stop]
    exp = rows["expected"][start:stop]; bad = rows["is_bad"][start:stop]; ids = rows["task_id"][start:stop]
    seq = rows["seq"][start:stop]
    is_canary = np.zeros(len(dur), dtype=bool)
    if canary and canary.get("share", 0) > 0:
        every = max(1, int(round(1.0 / canary["share"])))
        is_canary = canary_mask(seq, every, agent, canary.get("key"))
    dl_task = np.where(is_canary, int(canary["deadline_ns"]) if canary else int(dl), int(dl))
    over = dur > dl_task
    tok_trap = (~over) & (tok > tc)
    mem_trap = (~over) & (~tok_trap) & (mem > mc)
    trapped = over | tok_trap | mem_trap
    burned = np.where(over, dl_task, dur)
    traps = []
    for i in np.flatnonzero(trapped):
        reason = "deadline_exceeded" if over[i] else ("token_exhausted" if tok_trap[i] else "memory_exceeded")
        ev = {"trap_id": str(ids[i]), "agent_id": agent, "boundary_layer": 3, "trap_reason": reason,
              "trap_outcome": "HALT", "expected_load": bool(exp[i]), "kernel_made": False, "canary": bool(is_canary[i])}
        if over[i]:
            ev["nanoseconds_since_deadline"] = int(dur[i] - dl_task[i])
        elif tok_trap[i]:
            ev["tokens_deficit"] = int(tok[i] - tc)
        else:
            ev["memory_requested_bytes"] = int(mem[i])
        traps.append(ev)
    completed = dur[~trapped]
    legit = bad == 0
    summary = {
        "tasks": int(len(dur)), "legit": int(legit.sum()),
        "legit_failed": int((legit & trapped & (exp == 0)).sum()),
        "expected_trapped": int((trapped & (exp == 1)).sum()),
        "bad": int((bad == 1).sum()), "bad_caught": int(((bad == 1) & trapped).sum()),
        "deadline_traps": int(over.sum()), "token_traps": int(tok_trap.sum()), "memory_traps": int(mem_trap.sum()),
        "bad_ns": int(burned[bad == 1].sum()), "total_ns": int(burned.sum()),
        # time accounting for the net-time metric (docs/RED_TEAM.md T2): what the killed legitimate tasks burned
        # before they were killed, and how long they would have run (the work a rerun has to repeat)
        "legit_killed_burn_ns": int(burned[legit & trapped & (exp == 0)].sum()),
        "legit_killed_dur_ns": int(dur[legit & trapped & (exp == 0)].sum()),
        "canary_tasks": int(is_canary.sum()), "canary_deadline_traps": int((is_canary & over).sum()),
        "bad_in_slice": int((bad == 1).sum()),
        "completed_median_ns": kernel_median(completed) if len(completed) else None,
        "completed_q": kernel_quantiles(completed, QUANTILE_POINTS) if len(completed) else None,
    }
    return traps, summary


def kernel_median(values) -> float:
    """The kernel example's median: the upper middle element, sorted[len / 2]. np.median averages the two middles
    on even counts and differed from the kernel on most slices (red team, docs/RED_TEAM.md K1)."""
    v = np.sort(np.asarray(values))
    return float(v[len(v) // 2])


def kernel_quantiles(values, points) -> list:
    """The kernel example's nearest-rank quantiles: index round((n - 1) p) with Rust's f64::round (half away from
    zero). numpy's "nearest" rounds half to even, which disagrees on every tie. QUANTILE_METHOD=<numpy method> keeps
    the numpy variants for the sensitivity check in docs/REPRODUCIBILITY.md."""
    method = os.environ.get("QUANTILE_METHOD", "kernel")
    if method != "kernel":
        return [float(x) for x in np.percentile(values, points, method=method)]
    v = np.sort(np.asarray(values)); n = len(v)
    return [float(v[int(math.floor((n - 1) * (p / 100.0) + 0.5))]) for p in points]


def totals(rows):
    keys = ("tasks", "legit", "legit_failed", "expected_trapped", "bad", "bad_caught", "deadline_traps", "token_traps",
            "memory_traps", "bad_ns", "total_ns", "legit_killed_burn_ns", "legit_killed_dur_ns")
    return {k: int(sum(r[k] for r in rows)) for k in keys}


def replay(trace, agents, defaults, policies, slice_n=250, tasks_per_agent=None):
    """trace: load_trace() output; agents: list of dicts with agent_id, profile, should_tighten;
    policies: {name: TrapPolicy factory}. Returns the same structure as stack_trace_replay.py writes."""
    names = [ag["agent_id"] for ag in agents]
    team_of = {ag["agent_id"]: ag.get("team") for ag in agents}
    n_tasks = tasks_per_agent or len(trace[names[0]]["seq"])
    n_slices = n_tasks // slice_n
    static = {n: [run_slice(n, trace[n], r * slice_n, (r + 1) * slice_n, defaults)[1] for r in range(n_slices)] for n in names}
    results = {"slices": n_slices, "slice_tasks": slice_n, "defaults": defaults, "policies": {}}
    for pname, factory in policies.items():
        try:
            policy_obj = factory(defaults=defaults)
        except TypeError:
            policy_obj = factory()
        gov_kwargs = dict(getattr(policy_obj, "governor_kwargs", {}) or {})    # round 11c: the breaker cap as a dial
        gov = Governor(store_path=tempfile.mkdtemp(prefix="sim-"), use_semantic=True, **gov_kwargs)
        if is_controller(policy_obj):
            bridge = policy_obj
        else:
            bridge = TrapBridge(gov, policy=policy_obj, defaults=defaults, is_expected=lambda ev: bool(ev.get("expected_load")),
                                agent_class=lambda a: team_of.get(a))
        governed = {n: [] for n in names}; first = {}; trajectory = {n: [] for n in names}; verdicts = {n: [] for n in names}
        for r in range(n_slices):
            for n in names:   # fixed order, as the kernel replay ingests
                limits = {}
                for x in SUFFIX:
                    cur = bridge.limit_for(n, LIMIT_TO_REASON[x])
                    limits[x] = float(defaults[x]) if cur is None else cur
                trajectory[n].append(limits["deadline_ns"])
                plan = bridge.canary_plan(n)
                traps, s = run_slice(n, trace[n], r * slice_n, (r + 1) * slice_n, limits, canary=plan)
                governed[n].append(s)
                before = len(bridge.verdicts)
                bridge.observe_transactions(n, s["tasks"], completed_median_ns=s["completed_median_ns"],
                                            completed_quantiles=s["completed_q"], canary_tasks=s["canary_tasks"],
                                            canary_deadline_traps=s["canary_deadline_traps"])
                for v in bridge.verdicts[before:]:
                    verdicts[n].append({"slice": r, "verdict": v["verdict"], "deadline_ns": v["deadline_ns"],
                                        "bad_in_window": int(sum(x["bad_in_slice"] for x in governed[n][-(policy_obj.validate_after or 1) - 1:-1]))})
                bridge.ingest(traps)
                if is_controller(bridge):
                    bridge.observe_slice(n, s)
                if n not in first and any((bridge.limit_for(n, LIMIT_TO_REASON[x]) or float(defaults[x])) < float(defaults[x])
                                          for x in SUFFIX):
                    first[n] = r
        per = {}
        for ag in agents:
            n = ag["agent_id"]; limits = {}
            if is_controller(bridge):
                cur = bridge.limit.get(n); orig = float(defaults["deadline_ns"])
                limits = {"deadline_ns": None if cur is None else {"limit": cur, "held": False, "pending": 0, "tightenings": 0,
                                                                   "min_limit": bridge.min_limit.get(n, orig)},
                          "tokens_capacity": None, "memory_capacity_bytes": None}
                tightened = bridge.min_limit.get(n, orig) < orig    # ever lowered (it may have recovered since)
                restore = []; decisions = 0
            else:
                for x in SUFFIX:
                    bid = bridge.boundary_id(n, x)
                    try:
                        st = gov.boundary_status(bid)
                    except KeyError:
                        st = None
                    limits[x] = None if st is None else {"limit": st["limit"], "held": st["held"],
                                                         "pending": st["pending_operator_requests"],
                                                         "tightenings": st["automatic_tightenings_since_acknowledgement"]}
                tightened = any(v and v["limit"] < float(defaults[k]) for k, v in limits.items())
                dbid = bridge.boundary_id(n, "deadline_ns")
                restore = [e["payload"] for e in gov.audit.find("signal")
                           if e["payload"].get("kind") == "loosen_requested" and e["payload"].get("boundary_id") == dbid
                           and str(e["payload"].get("idempotency_key", "")).startswith("review-")]
                # round 11d: every decision a person is asked for about this agent's deadline: breaker holds (one per
                # episode, audited as tightening_held) and restore requests of either kind (drift or validation)
                decisions = sum(1 for e in gov.audit.find("tightening_held") if e["payload"].get("boundary_id") == dbid)
                decisions += sum(1 for e in gov.audit.find("signal")
                                 if e["payload"].get("kind") == "loosen_requested" and e["payload"].get("boundary_id") == dbid)
            per[n] = {"profile": ag["profile"], "should_tighten": int(ag["should_tighten"]), "tightened": tightened,
                      "first_tightened_slice": first.get(n), "limits": limits,
                      "static": totals(static[n]), "governed": totals(governed[n]),
                      "deadline_trajectory_ns": trajectory[n], "verdicts": verdicts[n],
                      "restore_requested": bool(restore), "operator_decisions": int(decisions)}
        integrity_ok, _checks = gov.verify_governance_integrity()
        results["policies"][pname] = {"agents": per, "bridge_counts": dict(bridge.counts),
                                      "audit_chain_intact": gov.audit.verify()[0], "integrity_ok": bool(integrity_ok),
                                      "controller": is_controller(bridge)}
    return results


def load_set(csv_path):
    d = Path(csv_path).parent; tag = Path(csv_path).stem.replace("stack_trace_", "")
    manifest = json.loads((d / ("MANIFEST.json" if tag == "v1" else f"MANIFEST_{tag}.json")).read_text())
    agents = list(csv.DictReader(open(d / f"stack_agents_{tag}.csv")))
    return tag, manifest, agents


def score(pr):
    ags = pr["agents"]
    tp = sum(1 for v in ags.values() if v["should_tighten"] and v["tightened"])
    fn = sum(1 for v in ags.values() if v["should_tighten"] and not v["tightened"])
    fp = sum(1 for v in ags.values() if not v["should_tighten"] and v["tightened"])
    tn = sum(1 for v in ags.values() if not v["should_tighten"] and not v["tightened"])
    return tp, fn, fp, tn


def report(res):
    for pname, pr in res["policies"].items():
        tp, fn, fp, tn = score(pr)
        print(f"\n== {pname}: should-tighten agents tightened {tp}/{tp+fn}; should-not agents tightened {fp}/{fp+tn}; "
              f"audit intact {pr['audit_chain_intact']}; counts {pr['bridge_counts']}")
        print("%-20s %-22s %3s %-4s %-9s %-24s %-22s" % ("agent", "profile", "sh", "tgt", "1st slice", "legit timeouts s->g", "bad time s->g (s)"))
        for n, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            print("%-20s %-22s %3d %-4s %-9s %6.2f%% -> %6.2f%%      %8.1f -> %8.1f" % (
                n, v["profile"], v["should_tighten"], "YES" if v["tightened"] else "no", v["first_tightened_slice"],
                100 * s["legit_failed"] / max(1, s["legit"]), 100 * g["legit_failed"] / max(1, g["legit"]),
                s["bad_ns"] / 1e9, g["bad_ns"] / 1e9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "testdata/traces/stack_trace_v1.csv"))
    ap.add_argument("--slice", type=int, default=250)
    ap.add_argument("--policies", default=",".join(POLICIES))
    ap.add_argument("--out", default=None); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    tag, manifest, agents = load_set(a.csv)
    trace = load_trace(a.csv)
    res = replay(trace, agents, manifest["defaults"], {p: POLICIES[p] for p in a.policies.split(",")},
                 slice_n=a.slice, tasks_per_agent=manifest["tasks_per_agent"])
    res.update({"csv": os.path.relpath(a.csv, ROOT), "sha256": manifest["sha256"], "kernel": "python model (stack_trace_sim.py)"})
    out = Path(a.out) if a.out else ROOT / "experiments" / f"stack_trace_sim_results_{tag}.json"
    for pr in res["policies"].values():
        for v in pr["agents"].values():
            v.pop("deadline_trajectory_ns", None)
    out.write_text(json.dumps(res, indent=1))
    if not a.quiet:
        report(res)


if __name__ == "__main__":
    main()
