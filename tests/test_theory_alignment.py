"""
Tests that the prototype implements the behaviors the theory document requires:

- Circuit breaker: repeated automatic tightening waits for a human (white paper sec. 4).
- Validation layer: an adaptation judged DEGRADED by an independent signal is rolled
  back, and a rollback that loosens is queued for a human, never applied automatically.
- Audit: operator decisions are written to the immutable store.
"""
import os

import pytest

from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.authority import AuthorizationResult
from src.governance.validation import ValidationOutcome


def _tighten(gov, boundary_id, current, factor=0.9):
    prop = gov.proposals.create_proposal(
        boundary_id, [], current, current * factor, "test tighten", AdaptationDirection.TIGHTEN,
    )
    approved, result = gov.authorize_proposal(prop)
    return prop, approved, result


@pytest.fixture
def gov(tmp_path):
    g = Governor(store_path=str(tmp_path / "store"))
    g.boundaries.create_boundary("cpu", "cpu", 100)
    return g


class TestCircuitBreaker:
    def test_automatic_tightening_stops_after_limit(self, gov):
        limit = 100
        for _ in range(Governor.AUTO_TIGHTEN_LIMIT):
            _, approved, result = _tighten(gov, "cpu", limit)
            assert result == AuthorizationResult.AUTO_APPROVED
            limit = gov.apply_approved_proposal(approved).current_limit

        prop, approved, result = _tighten(gov, "cpu", limit)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW
        assert approved.status.value == "pending"
        assert gov.boundaries.get_boundary("cpu").current_limit == limit

    def test_held_tightening_appears_in_review_queue(self, gov):
        limit = 100
        for _ in range(Governor.AUTO_TIGHTEN_LIMIT):
            _, approved, _ = _tighten(gov, "cpu", limit)
            limit = gov.apply_approved_proposal(approved).current_limit
        prop, _, _ = _tighten(gov, "cpu", limit)
        assert prop.proposal_id in [p.proposal_id for p in gov.list_pending_review()]

    def test_human_decision_resets_breaker(self, gov):
        limit = 100
        for _ in range(Governor.AUTO_TIGHTEN_LIMIT):
            _, approved, _ = _tighten(gov, "cpu", limit)
            limit = gov.apply_approved_proposal(approved).current_limit
        held, _, _ = _tighten(gov, "cpu", limit)
        gov.apply_operator_decision(held.proposal_id, "approve", "alice", "reviewed")
        limit = gov.boundaries.get_boundary("cpu").current_limit

        _, approved, result = _tighten(gov, "cpu", limit)
        assert result == AuthorizationResult.AUTO_APPROVED

    def test_breaker_is_per_boundary(self, gov):
        gov.boundaries.create_boundary("mem", "mem", 100)
        limit = 100
        for _ in range(Governor.AUTO_TIGHTEN_LIMIT):
            _, approved, _ = _tighten(gov, "cpu", limit)
            limit = gov.apply_approved_proposal(approved).current_limit
        _, _, result = _tighten(gov, "mem", 100)
        assert result == AuthorizationResult.AUTO_APPROVED


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


class TestInterpretationLayer:
    """Violations are interpreted before tightening: a registered legitimate load is not
    tightened into failure (docs/LIMITATIONS.md, critical limitation 1)."""

    def _expected_load(self):
        from src.governance.workload import ExpectedLoadPattern, WorkloadType
        return ExpectedLoadPattern(
            pattern_id="nightly-batch", boundary_id="cpu", resource_or_action="cpu",
            workload_type=WorkloadType.BATCH_JOB, description="nightly batch at 80% cpu",
            expected_value_range=(75, 85), expected_duration_seconds=3600,
            schedule="nightly", severity="low",
        )

    def test_semantic_layer_is_on_by_default(self, gov):
        assert gov.use_semantic is True
        assert gov.classifier is not None

    def test_expected_load_does_not_trigger_tightening(self, gov):
        gov.classifier.register_expected_pattern(self._expected_load())
        for _ in range(5):
            gov.execute_against_boundary("cpu", 80)
        assert gov.detect_and_propose_adaptation("cpu") is None

    def test_unexplained_violations_still_trigger_tightening(self, gov):
        gov.classifier.register_expected_pattern(self._expected_load())
        for _ in range(5):
            gov.execute_against_boundary("cpu", 150)
        proposal = gov.detect_and_propose_adaptation("cpu")
        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN
