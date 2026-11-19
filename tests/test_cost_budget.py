"""Cost budget (integrations/stack_traps.py, docs/CERTIFICATE.md): a deadline tightening never goes below the agent's own
(1 - budget) completed-duration quantile, and nothing tightens until that quantile is known."""
import itertools
import uuid

import pytest

from integrations.stack_traps import COST_BUDGET_POINT, TrapBridge, TrapPolicy
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

D = 100_000_000
DEFAULTS = {"deadline_ns": D, "tokens_capacity": 4000, "memory_capacity_bytes": 512 * 2**20}
_ids = itertools.count()


def make(tmp_path, **policy):
    ops = OperatorRegistry(iterations=1000); ops.register("alice", "alice-secret-1")
    g = Governor(store_path=str(tmp_path / "ev"), operators=ops)
    pol = TrapPolicy(min_traps=3, min_rate=0.01, rate_window_tasks=250, reasons=("deadline_exceeded",), **policy)
    return TrapBridge(g, defaults=DEFAULTS, policy=pol)


def trap(agent="a1"):
    return {"trap_id": f"t{next(_ids)}-{uuid.uuid4().hex[:6]}", "agent_id": agent, "boundary_layer": 3,
            "trap_reason": "deadline_exceeded", "trap_outcome": "HALT", "nanoseconds_since_deadline": 5}


def slice_(b, agent, q99_ns, n_traps=4, n=250):
    """One reported slice whose completed durations have the given 99th percentile, with n_traps deadline traps."""
    q = [q99_ns * 0.2, q99_ns * 0.4, q99_ns * 0.6, q99_ns * 0.8, q99_ns]
    b.observe_transactions(agent, n, completed_median_ns=q[0], completed_quantiles=q)
    return b.ingest([trap(agent) for _ in range(n_traps)])


def test_budget_must_be_a_supported_value(tmp_path):
    with pytest.raises(ValueError):
        TrapPolicy(cost_budget=0.003)
    assert set(COST_BUDGET_POINT) == {0.01, 0.05, 0.10, 0.25, 0.50}


def test_off_by_default_it_tightens_as_before(tmp_path):
    b = make(tmp_path)
    slice_(b, "a1", 10_000_000)
    assert b.limit_for("a1", "deadline_exceeded") == pytest.approx(0.9 * D)


def test_nothing_tightens_until_enough_slices_are_known(tmp_path):
    b = make(tmp_path, cost_budget=0.01)
    for _ in range(4):
        assert slice_(b, "a1", 10_000_000) == []
    assert b.counts["budget_unknown"] >= 1
    assert b.limit_for("a1", "deadline_exceeded") == float(D)


def test_a_fast_agent_tightens_to_the_cap(tmp_path):
    b = make(tmp_path, cost_budget=0.01)
    for _ in range(40):
        slice_(b, "a1", 10_000_000)
    assert b.limit_for("a1", "deadline_exceeded") == pytest.approx(0.729 * D)      # the breaker cap, not the budget, binds


def test_a_slow_tail_agent_is_held_above_its_own_p99(tmp_path):
    b = make(tmp_path, cost_budget=0.01)
    for _ in range(40):
        slice_(b, "a1", 95_000_000)                                                # 99th percentile at 95% of the deadline
    limit = b.limit_for("a1", "deadline_exceeded")
    assert limit >= 95_000_000 * 0.999
    assert limit < D                                                                # it did tighten, once, down to the floor
    assert b.counts["budget_clamped"] >= 1 and b.counts["budget_held"] >= 1


def test_a_tail_above_the_deadline_is_never_tightened(tmp_path):
    b = make(tmp_path, cost_budget=0.01)
    for _ in range(20):
        slice_(b, "a1", 120_000_000)
    assert b.limit_for("a1", "deadline_exceeded") == float(D)


def test_budget_never_loosens_a_limit_and_never_goes_below_the_cap(tmp_path):
    b = make(tmp_path, cost_budget=0.05)
    last = float(D)
    for i in range(60):
        slice_(b, "a1", 5_000_000 if i % 2 else 60_000_000)
        cur = b.limit_for("a1", "deadline_exceeded")
        assert cur <= last + 1e-6 and cur >= 0.729 * D - 1e-6
        last = cur
