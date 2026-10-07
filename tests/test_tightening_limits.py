"""Opt-in limits on automatic tightening (docs/THREAT_MODEL.md, attack T3)."""
import pytest

from src.governance.governor import Governor


def _drive(g, n, bid="api"):
    applied = 0
    for _ in range(n):
        lim = g.boundaries.get_boundary(bid).current_limit
        g.execute_against_boundary(bid, lim * 1.05)
        p = g.detect_and_propose_adaptation(bid)
        if p:
            p2, _ = g.authorize_proposal(p)
            g.apply_approved_proposal(p2)
            applied += 1
    return applied


def make(tmp_path, **kw):
    now = [0.0]
    g = Governor(store_path=str(tmp_path), clock=lambda: now[0], **kw)
    g.boundaries.create_boundary("api", "rate", 100.0)
    return g, now


def test_no_limit_old_evidence_retriggers(tmp_path):
    # With the breaker explicitly off, every violation after the third tightens again.
    g, _ = make(tmp_path, max_auto_tightenings=None)
    assert _drive(g, 17) == 15


def test_default_breaker_is_on(tmp_path):
    g, _ = make(tmp_path)
    assert g.max_auto_tightenings == Governor.DEFAULT_MAX_AUTO_TIGHTENINGS
    assert _drive(g, 17) == Governor.DEFAULT_MAX_AUTO_TIGHTENINGS


def test_fresh_evidence_needs_new_violations(tmp_path):
    g, _ = make(tmp_path, require_fresh_evidence=True)
    assert _drive(g, 9) == 3  # three new violations per tightening


def test_breaker_caps_automatic_tightenings_and_logs_holds(tmp_path):
    g, _ = make(tmp_path, require_fresh_evidence=True, max_auto_tightenings=2)
    assert _drive(g, 100) == 2
    assert g.boundaries.get_boundary("api").current_limit == pytest.approx(81.0)
    assert g.tightening_holds and "human must review" in g.tightening_holds[-1]["reason"]


def test_acknowledgement_reopens_the_breaker_and_requires_an_operator(tmp_path):
    g, _ = make(tmp_path, require_fresh_evidence=True, max_auto_tightenings=1)
    assert _drive(g, 30) == 1
    with pytest.raises(ValueError):
        g.acknowledge_tightening("api", "")
    g.acknowledge_tightening("api", "op-1")
    assert _drive(g, 30) == 1


def test_cooldown_blocks_until_time_passes(tmp_path):
    g, now = make(tmp_path, require_fresh_evidence=True, tighten_cooldown_s=100.0)
    assert _drive(g, 9) == 1
    now[0] += 101.0
    assert _drive(g, 3) == 1


def test_limits_never_loosen_or_break_integrity(tmp_path):
    g, _ = make(tmp_path, require_fresh_evidence=True, max_auto_tightenings=3)
    _drive(g, 50)
    assert all(ok for _, ok in g.verify_governance_integrity()[1])
