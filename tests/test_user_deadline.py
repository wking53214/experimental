"""One adaptive one-way deadline per agent (round 23 purple team, the supported design): window, floor, one-way, audited, human loosening."""
import pytest
from integrations.stack_traps import TrapBridge, TrapPolicy
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

D = 100_000_000
DEFAULTS = {"deadline_ns": D, "tokens_capacity": 4000, "memory_capacity_bytes": 512 * 2**20}
MON, OPS = "monitor-secret-1", "alice-secret-1"


def make(tmp_path, **kw):
    src = OperatorRegistry(iterations=1000); src.register("monitor", MON)
    ops = OperatorRegistry(iterations=1000); ops.register("alice", OPS)
    g = Governor(store_path=str(tmp_path / "ev"), signal_sources=src, operators=ops)
    return TrapBridge(g, defaults=DEFAULTS, policy=TrapPolicy(user_deadline=True, **kw), source="monitor", credential=MON), g


def test_off_by_default_and_class_path_unchanged(tmp_path):
    ops = OperatorRegistry(iterations=1000)
    b = TrapBridge(Governor(store_path=str(tmp_path / "e"), operators=ops), defaults=DEFAULTS)
    assert b.observe_user_durations("a", [1.0] * 50) == 0
    assert b.refresh_user_deadline("a") is None and b.user_deadline("a") == D


def test_needs_enough_completions_and_audits_the_wait(tmp_path):
    b, g = make(tmp_path)
    b.observe_user_durations("a", [10e6] * 29)
    assert b.refresh_user_deadline("a") is None and b.user_deadline("a") == D
    assert any(e["kind"] == "user_deadline_unknown" for e in g.audit.entries)


def test_tightens_to_margin_times_p99_and_never_loosens(tmp_path):
    b, g = make(tmp_path)
    b.observe_user_durations("a", [40e6] * 40)
    assert b.refresh_user_deadline("a") == pytest.approx(60e6)
    b.observe_user_durations("a", [90e6] * 400)                 # history gets slower: target rises, deadline must not
    assert b.refresh_user_deadline("a") is None
    assert b.user_deadline("a") == pytest.approx(60e6)
    assert any(e["kind"] == "user_deadline_held" for e in g.audit.entries)


def test_floor_and_ceiling(tmp_path):
    b, _ = make(tmp_path)
    b.observe_user_durations("a", [1.0] * 40)
    b.refresh_user_deadline("a")
    assert b.user_deadline("a") >= 0.25 * D - 1e-6
    b2, _ = make(tmp_path / "x")
    b2.observe_user_durations("z", [1e18] * 40)
    b2.refresh_user_deadline("z")
    assert b2.user_deadline("z") == D


def test_junk_durations_are_ignored(tmp_path):
    b, _ = make(tmp_path)
    for junk in ([float("nan")] * 50, [-1.0] * 50, [0.0] * 50, [float("inf")] * 50, [True] * 50, ["9"] * 50):
        assert b.observe_user_durations("a", junk) == 0


def test_window_lets_early_poisoning_age_out_but_unbounded_history_does_not(tmp_path):
    # 30 slow calls first (0.9 D), then 1,500 fast honest ones.
    def run(path, window):
        b, _ = make(path, user_window=window)
        b.observe_user_durations("a", [0.9 * D] * 30)
        b.refresh_user_deadline("a")
        for _ in range(15):
            b.observe_user_durations("a", [20e6] * 100)
            b.refresh_user_deadline("a")
        return b.user_deadline("a")
    assert run(tmp_path / "w", 1000) < 0.5 * D          # poison aged out of the window: tightened
    assert run(tmp_path / "u", 100_000) == D            # same stream, effectively unbounded history: still blocked


def test_loosening_needs_a_named_person(tmp_path):
    b, g = make(tmp_path)
    b.observe_user_durations("a", [40e6] * 40)
    b.refresh_user_deadline("a")
    base = b.user_deadline("a")
    r = b.request_user_loosening("a", D, "slow upstream")
    assert r["status"] == "pending_review" and b.user_deadline("a") == base
    g.apply_operator_decision(r["proposal_id"], "approve_loosen", "alice", "ok", credential=OPS)
    assert b.user_deadline("a") == pytest.approx(D)
    assert b.request_user_loosening("never-seen", D)["status"] == "rejected"


def test_audit_failure_changes_nothing_else(tmp_path, monkeypatch):
    b, g = make(tmp_path)
    b.observe_user_durations("a", [10e6] * 10)
    monkeypatch.setattr(g.audit, "append", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert b.refresh_user_deadline("a") is None
    assert b.counts["audit_write_failed"] == 1 and b.user_deadline("a") == D


def test_policy_validation():
    with pytest.raises(ValueError):
        TrapPolicy(user_deadline=True, user_floor=0.0)
    with pytest.raises(ValueError):
        TrapPolicy(user_deadline=True, user_min_completions=50, user_window=10)
