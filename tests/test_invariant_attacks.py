"""
Attacks on the core invariant: no automatic path may raise a limit.

TestObservationOnlyAttacker: the threat model the thesis targets (an attacker who
controls only the observations fed to the governor).

TestApiBypassesAreClosed / TestGrants / TestRollbackIsGated: ways a bug or misuse of the
public API used to be able to raise a limit without a human. Scope: this stops bugs and
misuse; Python cannot stop malicious code in the same process from forging a grant, which
is why the integrity check audits the stored version history.
"""
import random

import pytest

from src.governance.authority import AuthorizationResult
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection


def _gov(tmp_path, limit=100, semantic=True):
    g = Governor(store_path=str(tmp_path), use_semantic=semantic)
    g.boundaries.create_boundary("b", "cpu", limit)
    return g


def _limit(g):
    return g.boundaries.get_boundary("b").current_limit


class TestObservationOnlyAttacker:
    WEIRD = [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, 1e308, -1e308, 1e-308, 5e-324]

    def test_fuzzed_observations_never_raise_a_limit(self, tmp_path):
        rng = random.Random(0)
        for ep in range(60):
            g = Governor(store_path=str(tmp_path / f"ep{ep}"), use_semantic=bool(ep % 2))
            start = rng.choice([1, 10, 100, 1000, 1e6])
            g.boundaries.create_boundary("b", "r", start)
            g.patterns.create_pattern("b_p", "b", 2, 30)
            prev = start
            for _ in range(50):
                cur = _limit(g)
                v = rng.choice(self.WEIRD) if rng.random() < 0.2 else cur * rng.uniform(0.0, 3.0)
                try:
                    _, viol = g.execute_against_boundary("b", v)
                    if viol:
                        prop = g.detect_and_propose_adaptation("b")
                        if prop:
                            _, res = g.authorize_proposal(prop)
                            if res == AuthorizationResult.AUTO_APPROVED:
                                g.apply_approved_proposal(prop)
                except Exception:
                    pass
                now = _limit(g)
                assert now <= prev, "limit was raised by observations alone"
                assert now >= start * 0.2 - 1e-9, "limit fell below the 20% usability floor"
                prev = now
            assert g.authority.verify_no_auto_loosen()


class TestApiBypassesAreClosed:
    """Each of these used to raise a limit without a human (and the integrity check did
    not notice). They are regression tests for the fixes."""

    def test_mislabelled_tighten_cannot_raise_limit(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.TIGHTEN)
        _, result = g.authorize_proposal(p)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW
        with pytest.raises(ValueError):
            g.apply_approved_proposal(p)  # never approved
        assert _limit(g) == before

    def test_proposal_that_lies_about_its_current_value_cannot_loosen(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        # claims the limit is 1000, so 150 looks like a tightening; the real limit is 100
        p = g.proposals.create_proposal("b", [], 1000, before * 1.5, "x", AdaptationDirection.TIGHTEN)
        _, result = g.authorize_proposal(p)
        assert result == AuthorizationResult.AUTO_APPROVED  # judged on its own (false) values
        with pytest.raises(PermissionError):
            g.apply_approved_proposal(p)  # but checked against the real limit when applied
        assert _limit(g) == before

    def test_update_boundary_refuses_to_raise_limit_without_a_grant(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        with pytest.raises(PermissionError):
            g.boundaries.update_boundary("b", before * 10)
        assert _limit(g) == before

    def test_loosen_cannot_be_approved_without_a_human(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.LOOSEN)
        g.authorize_proposal(p)
        g.proposals.mark_approved(p.proposal_id)  # skips the authority entirely
        with pytest.raises(PermissionError):
            g.apply_approved_proposal(p)
        assert _limit(g) == before

    def test_mutating_direction_after_creation_does_not_help(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.LOOSEN)
        p.direction = AdaptationDirection.TIGHTEN
        _, result = g.authorize_proposal(p)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW
        assert _limit(g) == before

    def test_clearing_the_decision_log_does_not_hide_a_raised_limit(self, tmp_path):
        g = _gov(tmp_path)
        # tamper with the stored history directly, bypassing update_boundary
        from src.governance.boundary import BoundaryVersion, BoundaryStatus
        hist = g.boundaries.get_boundary_history("b")
        cur = hist.get_active_version()
        hist.versions[cur.version] = BoundaryVersion(
            cur.boundary_id, cur.version, cur.resource_or_action, cur.current_limit,
            BoundaryStatus.SUPERSEDED, cur.created_at, cur.parent_version)
        hist.add_version(BoundaryVersion("b", cur.version + 1, cur.resource_or_action,
                                         cur.current_limit * 10, BoundaryStatus.ACTIVE,
                                         0.0, cur.version))
        g.authority.decisions.clear()
        ok, checks = g.verify_governance_integrity()
        assert ok is False
        assert any("version history" in name and not passed for name, passed in checks)


class TestGrants:
    def test_human_approved_loosen_works_and_passes_the_audit(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 1.5, "growth", AdaptationDirection.LOOSEN)
        g.submit_for_review(p)
        g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", "capacity added")
        assert _limit(g) == before * 1.5
        ok, _ = g.verify_governance_integrity()
        assert ok

    def test_grant_is_single_use(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.LOOSEN)
        g.submit_for_review(p)
        g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice")
        grant = g.authority.grants[p.proposal_id]
        # tighten back, then try to replay the same grant to loosen again
        g.boundaries.update_boundary("b", before)
        with pytest.raises(PermissionError):
            g.boundaries.update_boundary("b", before * 2, grant=grant)

    def test_grant_only_works_for_its_own_boundary_and_value(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.LOOSEN)
        g.submit_for_review(p)
        g.authority.record_operator_decision(p, AuthorizationResult.OPERATOR_APPROVED, "alice")
        grant = g.authority.grants[p.proposal_id]
        with pytest.raises(PermissionError):
            g.boundaries.update_boundary("b", before * 5, grant=grant)  # different value
        assert _limit(g) == before

    def test_grants_cannot_be_constructed_outside_the_authority(self):
        from src.governance.grant import AuthorizationGrant
        with pytest.raises(PermissionError):
            AuthorizationGrant(object(), "p", "b", 5, "mallory")

    def test_operator_decision_requires_an_operator_id(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.LOOSEN)
        g.submit_for_review(p)
        with pytest.raises(ValueError):
            g.apply_operator_decision(p.proposal_id, "approve_loosen", "  ")

    def test_non_numeric_and_nan_limits_are_treated_as_loosening(self, tmp_path):
        g = _gov(tmp_path)
        for bad in (float("nan"), "unlimited", None):
            with pytest.raises(PermissionError):
                g.boundaries.update_boundary("b", bad)


class TestUsabilityFloor:
    def test_auto_tightening_cannot_go_below_the_floor(self, tmp_path):
        g = _gov(tmp_path)
        p = g.proposals.create_proposal("b", [], 100, 5, "x", AdaptationDirection.TIGHTEN)
        g.authorize_proposal(p)
        with pytest.raises(ValueError, match="usability floor"):
            g.apply_approved_proposal(p)
        assert _limit(g) == 100


class TestRollbackIsGated:
    def _tightened(self, tmp_path):
        from src.governance.rollback import RollbackExecutor
        g = _gov(tmp_path)
        p = g.proposals.create_proposal("b", [], 100, 90, "tighten", AdaptationDirection.TIGHTEN)
        g.authorize_proposal(p)
        g.apply_approved_proposal(p)
        return g, RollbackExecutor(g)

    def test_system_initiated_rollback_cannot_raise_the_limit(self, tmp_path):
        from src.governance.rollback import RollbackReason
        g, ex = self._tightened(tmp_path)
        assert ex.execute_rollback("p", "b", RollbackReason.DEGRADED_METRICS) is None
        assert _limit(g) == 90
        assert len(ex.queued_for_review) == 1
        assert g.list_pending_review()

    def test_operator_initiated_rollback_is_applied_and_attributed(self, tmp_path):
        from src.governance.rollback import RollbackReason
        g, ex = self._tightened(tmp_path)
        d = ex.execute_rollback("p", "b", RollbackReason.DEGRADED_METRICS, operator_id="alice")
        assert d is not None and _limit(g) == 100
        assert d.notes["initiated_by"] == "alice"
        assert g.verify_governance_integrity()[0]
