"""
Tests that the prototype implements the behaviors the theory document requires:

- Circuit breaker: repeated automatic tightening waits for a human (white paper sec. 4).
  Uses the opt-in limit from main (max_auto_tightenings / acknowledge_tightening).
- Validation layer: an adaptation judged DEGRADED by an independent signal is rolled
  back, and a rollback that loosens is queued for a human, never applied automatically.
- Interpretation layer: violations inside a registered expected load do not drive tightening.
- Audit: operator decisions are written to the immutable store.

These options are opt-in on Governor (all off by default); each test sets them explicitly.
"""
import os

import pytest

from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.validation import ValidationOutcome


def _tighten(gov, boundary_id, current, factor=0.9):
    prop = gov.proposals.create_proposal(
        boundary_id, [], current, current * factor, "test tighten", AdaptationDirection.TIGHTEN,
    )
    approved, result = gov.authorize_proposal(prop)
    return prop, approved, result


def _drive(gov, boundary_id, n):
    """Feed violations and let the governor propose and apply tightenings, n rounds."""
    applied = 0
    for _ in range(n):
        lim = gov.boundaries.get_boundary(boundary_id).current_limit
        gov.execute_against_boundary(boundary_id, lim * 1.05)
        p = gov.detect_and_propose_adaptation(boundary_id)
        if p:
            approved, _ = gov.authorize_proposal(p)
            gov.apply_approved_proposal(approved)
            applied += 1
    return applied


@pytest.fixture
def gov(tmp_path):
    g = Governor(store_path=str(tmp_path / "store"))
    g.boundaries.create_boundary("cpu", "cpu", 100)
    return g


class TestCircuitBreaker:
    def test_breaker_holds_tightening_after_limit(self, tmp_path):
        g = Governor(store_path=str(tmp_path / "s"), max_auto_tightenings=3)
        g.boundaries.create_boundary("cpu", "cpu", 100)
        assert _drive(g, "cpu", 10) == 3
        assert g.tightening_holds, "held tightenings are recorded"
        assert "human" in g.tightening_holds[-1]["reason"]

    def test_acknowledgement_by_operator_resumes_tightening(self, tmp_path):
        g = Governor(store_path=str(tmp_path / "s"), max_auto_tightenings=3)
        g.boundaries.create_boundary("cpu", "cpu", 100)
        _drive(g, "cpu", 10)
        with pytest.raises(ValueError):
            g.acknowledge_tightening("cpu", "")
        g.acknowledge_tightening("cpu", "alice")
        assert _drive(g, "cpu", 2) == 2

    def test_breaker_is_per_boundary(self, tmp_path):
        g = Governor(store_path=str(tmp_path / "s"), max_auto_tightenings=3)
        g.boundaries.create_boundary("cpu", "cpu", 100)
        g.boundaries.create_boundary("mem", "mem", 100)
        _drive(g, "cpu", 10)
        # cpu has used up its three; mem still gets its own three before it is held.
        assert _drive(g, "mem", 10) == 3

    def test_on_by_default(self, tmp_path):
        g = Governor(store_path=str(tmp_path / "s"))
        g.boundaries.create_boundary("cpu", "cpu", 100)
        assert _drive(g, "cpu", 10) == Governor.DEFAULT_MAX_AUTO_TIGHTENINGS
        assert g.tightening_holds

    def test_can_be_turned_off(self, tmp_path):
        g = Governor(store_path=str(tmp_path / "s"), max_auto_tightenings=None)
        g.boundaries.create_boundary("cpu", "cpu", 100)
        assert _drive(g, "cpu", 10) > Governor.DEFAULT_MAX_AUTO_TIGHTENINGS
        assert not g.tightening_holds


class TestValidationDrivenRollback:
    def _tightened_boundary(self, gov):
        _, approved, _ = _tighten(gov, "cpu", 100)
        gov.apply_approved_proposal(approved)
        return approved

    def test_degraded_outcome_queues_rollback_for_human(self, gov):
        applied = self._tightened_boundary(gov)
        gov.validators.register_validator("cpu", lambda state: ValidationOutcome.DEGRADED)

        outcome, decision = gov.review_effectiveness(applied.proposal_id, {"p99": 900})

        assert outcome == ValidationOutcome.DEGRADED
        assert decision is None  # loosening rollback is not applied automatically
        assert gov.rollback.queued_for_review
        assert gov.boundaries.get_boundary("cpu").current_limit == 90

    def test_improved_outcome_does_not_roll_back(self, gov):
        applied = self._tightened_boundary(gov)
        gov.validators.register_validator("cpu", lambda state: ValidationOutcome.IMPROVED)

        outcome, decision = gov.review_effectiveness(applied.proposal_id, {"p99": 40})

        assert outcome == ValidationOutcome.IMPROVED
        assert decision is None
        assert not gov.rollback.queued_for_review

    def test_missing_validator_is_unknown_not_rollback(self, gov):
        applied = self._tightened_boundary(gov)
        outcome, decision = gov.review_effectiveness(applied.proposal_id, {})
        assert outcome == ValidationOutcome.UNKNOWN
        assert decision is None
        assert not gov.rollback.queued_for_review


class TestInterpretationLayer:
    """Violations are interpreted before tightening: a registered legitimate load is not
    tightened into failure (docs/LIMITATIONS.md, critical limitation 1)."""

    @pytest.fixture
    def semantic(self, tmp_path):
        g = Governor(store_path=str(tmp_path / "store"), use_semantic=True)
        g.boundaries.create_boundary("cpu", "cpu", 100)
        return g

    def _expected_load(self):
        from src.governance.workload import ExpectedLoadPattern, WorkloadType
        return ExpectedLoadPattern(
            pattern_id="nightly-batch", boundary_id="cpu", resource_or_action="cpu",
            workload_type=WorkloadType.BATCH_JOB, description="nightly batch at 80% cpu",
            expected_value_range=(75, 85), expected_duration_seconds=3600,
            schedule="nightly", severity="low",
        )

    def test_expected_load_does_not_trigger_tightening(self, semantic):
        semantic.classifier.register_expected_pattern(self._expected_load())
        for _ in range(5):
            semantic.execute_against_boundary("cpu", 80)
        assert semantic.detect_and_propose_adaptation("cpu") is None

    def test_unexplained_violations_still_trigger_tightening(self, semantic):
        semantic.classifier.register_expected_pattern(self._expected_load())
        for _ in range(5):
            semantic.execute_against_boundary("cpu", 150)
        proposal = semantic.detect_and_propose_adaptation("cpu")
        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN


class TestAuditTrail:
    def test_operator_decision_is_written_to_store(self, gov, tmp_path):
        prop = gov.proposals.create_proposal(
            "cpu", [], 100, 120, "loosen for review", AdaptationDirection.LOOSEN,
        )
        gov.submit_for_review(prop)
        gov.apply_operator_decision(prop.proposal_id, "approve_loosen", "alice", "ok")

        store_dir = tmp_path / "store"
        files = [f for _, _, names in os.walk(store_dir) for f in names]
        assert any("operator" in f for f in files)
        contents = " ".join(
            open(os.path.join(root, f)).read()
            for root, _, names in os.walk(store_dir) for f in names
        )
        assert "alice" in contents
