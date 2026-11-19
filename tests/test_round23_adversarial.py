"""Round 23 adversarial program, governance and reliability parts that run on the Python side (docs/TRACE_TEST_PLAN.md, S8, S10).
The kernel-side cases are in stack-p3-2/tests/class_deadlines.rs. Every loosening attempt below must leave the limit unchanged."""
import itertools

import pytest

from integrations.stack_traps import TrapBridge, TrapPolicy
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

D = 100_000_000
DEFAULTS = {"deadline_ns": D, "tokens_capacity": 4000, "memory_capacity_bytes": 512 * 2**20}
MON, OPS = "monitor-secret-1", "alice-secret-1"
ATTEMPTS = itertools.count()


def make(tmp_path, durable=False, **policy):
    sources = OperatorRegistry(iterations=1000); sources.register("monitor", MON)
    ops = OperatorRegistry(iterations=1000); ops.register("alice", OPS)
    extra = {"audit_path": str(tmp_path / "audit.jsonl")} if durable else {}
    g = Governor(store_path=str(tmp_path / "ev"), signal_sources=sources, operators=ops, **extra)
    b = TrapBridge(g, defaults=DEFAULTS, policy=TrapPolicy(class_deadlines=True, **policy), source="monitor", credential=MON)
    return b, g


def tightened(b, g, agent="a", cls="read", ms=40):
    b.observe_class_durations(agent, cls, [ms * 1e6] * 40)
    b.refresh_class_deadlines(agent)
    return b.class_deadline(agent, cls)


def test_loosening_without_authorization_changes_nothing(tmp_path):
    b, g = make(tmp_path)
    base = tightened(b, g)
    for i in range(100):
        next(ATTEMPTS)
        r = b.request_class_loosening("a", "read", D * (1 + i / 100), "please")
        assert r["status"] in ("pending_review", "rejected")
        assert b.class_deadline("a", "read") == base


def test_bad_or_missing_operator_credentials_cannot_approve(tmp_path):
    b, g = make(tmp_path)
    base = tightened(b, g)
    r = b.request_class_loosening("a", "read", D, "x")
    pid = r["proposal_id"]
    creds = [None, "", "wrong", MON, "alice-secret-2", OPS.upper(), " " + OPS, OPS + " "]
    for who in ("alice", "bob", "monitor", "", None):
        for c in creds:
            if who == "alice" and c == OPS:
                continue
            next(ATTEMPTS)
            with pytest.raises(Exception):
                g.apply_operator_decision(pid, "approve_loosen", who, "go", credential=c)
            assert b.class_deadline("a", "read") == base


def test_a_machine_source_cannot_decide_its_own_request(tmp_path):
    b, g = make(tmp_path)
    base = tightened(b, g)
    pid = b.request_class_loosening("a", "read", D, "x")["proposal_id"]
    for _ in range(40):
        next(ATTEMPTS)
        with pytest.raises(Exception):
            g.apply_operator_decision(pid, "approve_loosen", "monitor", "self-approve", credential=MON)
    assert b.class_deadline("a", "read") == base


def test_a_grant_is_single_use_and_replay_does_not_restore_the_old_limit(tmp_path):
    b, g = make(tmp_path)
    tightened(b, g)
    pid = b.request_class_loosening("a", "read", D, "slow upstream")["proposal_id"]
    g.apply_operator_decision(pid, "approve_loosen", "alice", "ok", credential=OPS)       # the legitimate loosening
    after = b.class_deadline("a", "read")
    assert after == pytest.approx(D)
    b.observe_class_durations("a", "read", [30e6] * 500)                                  # later tightening
    for k in range(20):
        b.refresh_class_deadlines("a")
    now = b.class_deadline("a", "read")
    for _ in range(25):                                                                   # replay the old approval
        next(ATTEMPTS)
        try:
            g.apply_operator_decision(pid, "approve_loosen", "alice", "again", credential=OPS)
        except Exception:
            pass
        assert b.class_deadline("a", "read") <= now + 1e-6


def test_the_adaptive_component_editing_its_own_policy_still_cannot_pass_the_governor_floor(tmp_path):
    b, g = make(tmp_path)
    b.policy.class_floor, b.policy.class_margin = 0.0, 1.0           # an adaptive component that rewrites its own limits
    b.observe_class_durations("a", "read", [1e6] * 40)
    for _ in range(10):
        next(ATTEMPTS)
        b.refresh_class_deadlines("a")
    # The service-side floor (20% of the original) holds; the kernel adds its own 25% floor (Rust test).
    assert b.class_deadline("a", "read") >= 0.2 * D - 1e-6


def test_direct_writes_to_the_store_cannot_loosen_without_a_grant(tmp_path):
    b, g = make(tmp_path)
    tightened(b, g)
    bid = b._class_boundary("a", "read")
    for lim in (D, D * 2, 1e18, float("inf")):
        next(ATTEMPTS)
        with pytest.raises(Exception):
            g.boundaries.update_boundary(bid, "agent_limit", lim)
        assert b.class_deadline("a", "read") < D


def test_creating_the_boundary_again_cannot_reset_it(tmp_path):
    b, g = make(tmp_path)
    base = tightened(b, g)
    for _ in range(10):
        next(ATTEMPTS)
        with pytest.raises(Exception):
            g.boundaries.create_boundary(b._class_boundary("a", "read"), "agent_limit", D)
        g.ensure_boundary(b._class_boundary("a", "read"), "agent_limit", D)               # ensure keeps the lower limit
    assert b.class_deadline("a", "read") == base


def test_restart_keeps_class_limits_and_loses_only_history(tmp_path):
    b, g = make(tmp_path, durable=True)
    base = tightened(b, g)
    b2, g2 = make(tmp_path, durable=True)                              # a new process on the same audit log
    b2.observe_class_durations("a", "read", [40e6])                    # class known again, one sample
    assert b2.class_deadline("a", "read") == pytest.approx(base)       # limit survived
    b2.observe_class_durations("a", "read", [95e6] * 100)              # history gone: relearns slowly
    b2.refresh_class_deadlines("a")
    assert b2.class_deadline("a", "read") <= base + 1e-6               # never loosens


def test_tampered_audit_log_stops_startup(tmp_path):
    b, g = make(tmp_path, durable=True)
    tightened(b, g)
    p = tmp_path / "audit.jsonl"
    lines = p.read_text().splitlines()
    lines[1] = lines[1].replace("\"tighten\"", "\"loosen\"")
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(Exception, match="failed verification"):
        make(tmp_path, durable=True)                 # the governor refuses to start on a broken hash chain


def test_missing_and_inconsistent_classes_fall_back(tmp_path):
    b, g = make(tmp_path)
    assert b.class_deadline("a", None) == D and b.class_deadline("a", "") == D and b.class_deadline(None, "read") == D
    assert b.class_deadline("a", "never-seen") == D
    tightened(b, g, cls="read")
    assert b.class_deadline("a", "read ") == D        # a differently spelled class is a different (unknown) class: baseline


def test_hostile_durations_cannot_force_a_looser_or_tighter_than_allowed_limit(tmp_path):
    b, g = make(tmp_path)
    for junk in ([float("nan")] * 50, [-1.0] * 50, [0.0] * 50, [float("inf")] * 50, [True] * 50, ["9"] * 50):
        next(ATTEMPTS)
        assert b.observe_class_durations("a", "x", junk) == 0
    b.observe_class_durations("a", "x", [1.0] * 40)                   # absurdly fast: floor applies
    b.refresh_class_deadlines("a")
    assert 0.25 * D - 1e-6 <= b.class_deadline("a", "x") <= D
    b.observe_class_durations("a", "y", [1e18] * 40)                  # absurdly slow: capped at the baseline
    b.refresh_class_deadlines("a")
    assert b.class_deadline("a", "y") == D


def test_attempt_count_meets_the_pre_registered_minimum():
    assert next(ATTEMPTS) >= 200
