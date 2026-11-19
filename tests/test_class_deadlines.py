"""Class-conditioned deadlines in TrapBridge (round 23): one-way, floored at class_floor x D, audited, loosened only by a person."""
import pytest
from integrations.stack_traps import TrapBridge, TrapPolicy
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

D = 100_000_000
DEFAULTS = {"deadline_ns": D, "tokens_capacity": 4000, "memory_capacity_bytes": 512 * 2**20}


def make(tmp_path, **kw):
    ops = OperatorRegistry(iterations=1000); ops.register("alice", "alice-secret-1")
    g = Governor(store_path=str(tmp_path / "ev"), operators=ops)
    return TrapBridge(g, defaults=DEFAULTS, policy=TrapPolicy(class_deadlines=True, **kw)), g


def test_off_by_default(tmp_path):
    ops = OperatorRegistry(iterations=1000)
    b = TrapBridge(Governor(store_path=str(tmp_path / "e"), operators=ops), defaults=DEFAULTS)
    assert b.observe_class_durations("a", "read", [1.0] * 50) == 0
    assert b.refresh_class_deadlines("a") == {}
    assert b.class_deadline("a", "read") == D


def test_needs_enough_completions(tmp_path):
    b, _ = make(tmp_path)
    b.observe_class_durations("a", "read", [10e6] * 29)
    assert b.refresh_class_deadlines("a") == {} and b.class_deadline("a", "read") == D


def test_tightens_to_margin_times_p99(tmp_path):
    b, _ = make(tmp_path)
    b.observe_class_durations("a", "read", [40e6] * 40)       # p99 = 40 ms, target = 60 ms
    assert b.refresh_class_deadlines("a")
    assert b.class_deadline("a", "read") == pytest.approx(60e6)
    assert b.class_deadline("a", "write") == D                # other classes untouched


def test_floor_and_ceiling(tmp_path):
    b, _ = make(tmp_path)
    b.observe_class_durations("a", "fast", [1e6] * 40)         # target 1.5 ms, floored at 25 ms
    b.observe_class_durations("a", "slow", [90e6] * 40)        # target 135 ms, capped at D
    b.refresh_class_deadlines("a")
    assert b.class_deadline("a", "fast") == pytest.approx(0.25 * D)
    assert b.class_deadline("a", "slow") == D


def test_never_loosens_by_itself(tmp_path):
    b, _ = make(tmp_path)
    b.observe_class_durations("a", "read", [40e6] * 40); b.refresh_class_deadlines("a")
    b.observe_class_durations("a", "read", [95e6] * 4000)      # poisoning / slow period
    b.refresh_class_deadlines("a")
    assert b.class_deadline("a", "read") <= 60e6 + 1


def test_loosening_waits_for_a_person(tmp_path):
    b, g = make(tmp_path)
    b.observe_class_durations("a", "read", [40e6] * 40); b.refresh_class_deadlines("a")
    r = b.request_class_loosening("a", "read", 90e6, "slow upstream")
    assert r["status"] == "pending_review"
    assert b.class_deadline("a", "read") == pytest.approx(60e6)   # unchanged until approved


def test_audit_records_tightening(tmp_path):
    b, g = make(tmp_path)
    b.observe_class_durations("a", "read", [40e6] * 40); b.refresh_class_deadlines("a")
    assert b.counts["class_tightened"] == 1
    assert any("class" in str(e) for e in g.audit.entries[-3:])


def test_junk_input_ignored(tmp_path):
    b, _ = make(tmp_path)
    assert b.observe_class_durations(None, "read", [1e6]) == 0
    assert b.observe_class_durations("a", "read", [float("nan"), -1, 0, True, "x"]) == 0


def test_class_flood_is_capped(tmp_path):
    b, _ = make(tmp_path, class_max_per_agent=3)
    for i in range(10):
        b.observe_class_durations("a", f"c{i}", [1e6])
    assert b.counts["class_cap"] == 7


def test_unsafe_class_names_are_hashed(tmp_path):
    b, _ = make(tmp_path)
    b.observe_class_durations("a", "../x y", [40e6] * 40)
    assert b.refresh_class_deadlines("a")
    assert b.class_deadline("a", "../x y") == pytest.approx(60e6)


def test_bad_config_rejected():
    with pytest.raises(ValueError):
        TrapPolicy(class_deadlines=True, class_floor=0.0)
