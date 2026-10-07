"""
Phase 2 Sprint 3: Automatic Rollback & Recovery Tests

Test automatic reversion when adaptations degrade outcomes.
Prevent rollback loops and escalate persistent failures.
"""
import pytest
import time
import uuid
from src.governance.rollback import (
    RollbackManager, RollbackExecutor, RollbackReason, RollbackPreventionRule
)
from src.governance.governor import Governor


class TestRollbackDecision:
    """Test rollback decision logic."""

    def test_rollback_triggered_on_degraded_high_confidence(self):
        """Rollback triggered when outcomes degraded with high confidence."""
        manager = RollbackManager()

        should_rollback = manager.should_rollback(
            boundary_id="test",
            effectiveness_outcome="degraded",
            confidence=0.9,
        )

        assert should_rollback == True

    def test_rollback_not_triggered_on_improved(self):
        """Rollback not triggered when outcomes improved."""
        manager = RollbackManager()

        should_rollback = manager.should_rollback(
            boundary_id="test",
            effectiveness_outcome="improved",
            confidence=0.9,
        )

        assert should_rollback == False

    def test_rollback_not_triggered_on_low_confidence(self):
        """Rollback not triggered on degraded with low confidence."""
        manager = RollbackManager()

        should_rollback = manager.should_rollback(
            boundary_id="test",
            effectiveness_outcome="degraded",
            confidence=0.5,  # Below 0.7 threshold
        )

        assert should_rollback == False

    def test_rollback_prevention_rule_creation(self):
        """Prevention rules can be registered."""
        manager = RollbackManager()

        rule = manager.register_prevention_rule(
            boundary_id="test",
            max_rollbacks_per_hour=3,
            min_time_between_rollbacks_seconds=300,
        )

        assert rule.boundary_id == "test"
        assert rule.max_rollbacks_per_hour == 3


class TestRollbackPrevention:
    """Test rollback prevention rules."""

    def test_prevent_rollback_cooling_off(self):
        """Prevent rollback if insufficient time passed since last rollback."""
        manager = RollbackManager()
        manager.register_prevention_rule(
            boundary_id="test",
            min_time_between_rollbacks_seconds=300,
        )

        # Record first rollback
        manager.create_rollback_decision(
            rollback_id=str(uuid.uuid4()),
            proposal_id="prop1",
            boundary_id="test",
            previous_version=2,
            reverted_to_version=1,
            reason=RollbackReason.DEGRADED_METRICS,
        )

        # Try to rollback again immediately (should fail)
        should_rollback = manager.should_rollback(
            boundary_id="test",
            effectiveness_outcome="degraded",
            confidence=0.9,
        )

        assert should_rollback == False

    def test_prevent_rollback_exceeding_limit(self):
        """Prevent rollback if too many recent rollbacks."""
        manager = RollbackManager()
        manager.register_prevention_rule(
            boundary_id="test",
            max_rollbacks_per_hour=2,
        )

        # Record 2 rollbacks
        for i in range(2):
            manager.create_rollback_decision(
                rollback_id=str(uuid.uuid4()),
                proposal_id=f"prop{i}",
                boundary_id="test",
                previous_version=i+2,
                reverted_to_version=i+1,
                reason=RollbackReason.DEGRADED_METRICS,
            )

        # Third rollback should fail (exceeds limit of 2)
        should_rollback = manager.should_rollback(
            boundary_id="test",
            effectiveness_outcome="degraded",
            confidence=0.9,
        )

        assert should_rollback == False


class TestRollbackExecution:
    """Test rollback execution."""

    def test_execute_rollback_reverts_boundary(self):
        """Executing rollback reverts boundary to previous version."""
        governor = Governor(
            store_path=f"/tmp/test_rollback_{int(time.time()*1000)}",
            use_semantic=False
        )

        executor = RollbackExecutor(governor)

        # Create initial boundary
        v1 = governor.boundaries.create_boundary(
            boundary_id="monitored",
            resource_or_action="cpu",
            initial_limit=80,
        )

        # Tighten it (creates v2)
        v2 = governor.boundaries.update_boundary(
            boundary_id="monitored",
            new_limit=72,
        )

        assert v2.version == 2
        assert v2.current_limit == 72

        # Execute rollback
        # Going back to a looser limit is a loosening: a named operator must initiate it
        decision = executor.execute_rollback(
            proposal_id="prop_that_backfired",
            boundary_id="monitored",
            reason=RollbackReason.DEGRADED_METRICS,
            operator_id="operator-1",
        )

        assert decision is not None
        assert decision.reverted_to_version == 3  # Creates new version pointing to old limit

        # Verify boundary reverted to v1's limit (80)
        current = governor.boundaries.get_boundary("monitored")
        assert current.current_limit == 80  # Reverted to v1's limit

    def test_cannot_rollback_initial_version(self):
        """Cannot rollback when at initial version."""
        governor = Governor(
            store_path=f"/tmp/test_rollback_initial_{int(time.time()*1000)}",
            use_semantic=False
        )

        executor = RollbackExecutor(governor)

        # Create boundary (version 1)
        governor.boundaries.create_boundary(
            boundary_id="test",
            resource_or_action="metric",
            initial_limit=100,
        )

        # Try to rollback (should return None, no previous version)
        decision = executor.execute_rollback(
            proposal_id="prop",
            boundary_id="test",
            reason=RollbackReason.DEGRADED_METRICS,
        )

        assert decision is None


class TestEscalation:
    """Test escalation of repeated rollbacks."""

    def test_escalate_on_many_rollbacks(self):
        """Escalate if too many rollbacks in short time."""
        manager = RollbackManager()

        # Simulate 6 rollbacks in last hour
        for i in range(6):
            manager.create_rollback_decision(
                rollback_id=str(uuid.uuid4()),
                proposal_id=f"prop{i}",
                boundary_id="test",
                previous_version=i+2,
                reverted_to_version=i+1,
                reason=RollbackReason.DEGRADED_METRICS,
            )

        # Should be escalated (6 > 5)
        is_escalated = manager.is_boundary_escalated("test")

        assert is_escalated == True

    def test_escalate_on_consecutive_rollbacks(self):
        """Escalate if 3+ consecutive rollbacks within 5 minutes."""
        manager = RollbackManager()

        # Create 3 rollbacks in quick succession
        base_time = time.time()
        for i in range(3):
            rb = manager.create_rollback_decision(
                rollback_id=str(uuid.uuid4()),
                proposal_id=f"prop{i}",
                boundary_id="test",
                previous_version=i+2,
                reverted_to_version=i+1,
                reason=RollbackReason.DEGRADED_METRICS,
            )
            # Fake timestamps to be within 5 minutes
            rb.timestamp = base_time + (i * 60)  # 1 minute apart

        is_escalated = manager.is_boundary_escalated("test")

        assert is_escalated == True


class TestRollbackHistory:
    """Test rollback history tracking."""

    def test_rollback_summary(self):
        """Get summary of rollbacks for boundary."""
        manager = RollbackManager()

        # Record rollbacks with different reasons
        for i, reason in enumerate([
            RollbackReason.DEGRADED_METRICS,
            RollbackReason.DEGRADED_METRICS,
            RollbackReason.SLO_VIOLATION,
        ]):
            manager.create_rollback_decision(
                rollback_id=str(uuid.uuid4()),
                proposal_id=f"prop{i}",
                boundary_id="test",
                previous_version=i+2,
                reverted_to_version=i+1,
                reason=reason,
            )

        summary = manager.get_boundary_rollback_summary("test")

        assert summary["total"] == 3
        assert summary["by_reason"]["degraded_metrics"] == 2
        assert summary["by_reason"]["slo_violation"] == 1
        assert summary["last_rollback"] is not None

    def test_get_rollback_by_proposal(self):
        """Retrieve rollback by proposal ID."""
        manager = RollbackManager()

        decision = manager.create_rollback_decision(
            rollback_id=str(uuid.uuid4()),
            proposal_id="target_proposal",
            boundary_id="test",
            previous_version=2,
            reverted_to_version=1,
            reason=RollbackReason.DEGRADED_METRICS,
        )

        retrieved = manager.get_rollback_by_proposal("target_proposal")

        assert retrieved is not None
        assert retrieved.rollback_id == decision.rollback_id


class TestRollbackReasons:
    """Test different rollback reasons."""

    def test_degraded_metrics_reason(self):
        """Record rollback for degraded metrics."""
        manager = RollbackManager()

        decision = manager.create_rollback_decision(
            rollback_id=str(uuid.uuid4()),
            proposal_id="prop",
            boundary_id="test",
            previous_version=2,
            reverted_to_version=1,
            reason=RollbackReason.DEGRADED_METRICS,
            metrics_before={"violation_rate": 0.15},
            metrics_after={"violation_rate": 0.25},
        )

        assert decision.reason == RollbackReason.DEGRADED_METRICS
        assert decision.metrics_before_adaptation["violation_rate"] == 0.15
        assert decision.metrics_after_adaptation["violation_rate"] == 0.25

    def test_slo_violation_reason(self):
        """Record rollback for SLO violation."""
        manager = RollbackManager()

        decision = manager.create_rollback_decision(
            rollback_id=str(uuid.uuid4()),
            proposal_id="prop",
            boundary_id="test",
            previous_version=2,
            reverted_to_version=1,
            reason=RollbackReason.SLO_VIOLATION,
        )

        assert decision.reason == RollbackReason.SLO_VIOLATION

    def test_post_rollback_metrics(self):
        """Record metrics after rollback to verify recovery."""
        manager = RollbackManager()

        decision = manager.create_rollback_decision(
            rollback_id=str(uuid.uuid4()),
            proposal_id="prop",
            boundary_id="test",
            previous_version=2,
            reverted_to_version=1,
            reason=RollbackReason.DEGRADED_METRICS,
        )

        # Record post-rollback metrics
        manager.record_post_rollback_metrics(
            decision.rollback_id,
            {"violation_rate": 0.16},
        )

        retrieved = manager.rollback_history[decision.rollback_id]
        assert retrieved.metrics_after_rollback["violation_rate"] == 0.16
