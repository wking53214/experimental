"""
Core governance integrity tests.

Tests 1-5, 10: Basic functionality and core invariants.
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.principle import GovernancePrinciple, PrincipleType
from src.governance.boundary import BoundaryStatus
from src.governance.proposal import AdaptationDirection
from src.governance.authority import AuthorizationResult
from src.governance.validation import ValidationOutcome


@pytest.fixture
def governor():
    """Create a fresh governor for each test."""
    return Governor(store_path="/tmp/test_governor")


class TestNormalExecution:
    """TEST 1: Normal execution does not modify boundary."""

    def test_compliant_execution(self, governor):
        """Compliant execution does not trigger adaptation."""
        # Create a boundary
        boundary = governor.boundaries.create_boundary(
            boundary_id="cpu_limit",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        # Create a pattern detector
        governor.patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_limit",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Execute with compliant value
        execution, violation = governor.execute_against_boundary(
            boundary_id="cpu_limit",
            observed_value=50,
        )

        # Verify no violation
        assert execution is not None
        assert violation is None

        # Verify no proposal generated
        proposal = governor.detect_and_propose_adaptation("cpu_limit")
        assert proposal is None

        # Verify boundary unchanged
        updated_boundary = governor.boundaries.get_boundary("cpu_limit")
        assert updated_boundary.version == boundary.version
        assert updated_boundary.current_limit == 80


class TestSingleViolation:
    """TEST 2: Single violation creates evidence but does not change boundary."""

    def test_single_violation_recorded(self, governor):
        """Single violation creates immutable evidence."""
        # Create boundary and pattern
        governor.boundaries.create_boundary(
            boundary_id="memory_limit",
            resource_or_action="memory_mb",
            initial_limit=1000,
        )
        governor.patterns.create_pattern(
            pattern_id="mem_pattern",
            boundary_id="memory_limit",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Execute with violation
        execution, violation = governor.execute_against_boundary(
            boundary_id="memory_limit",
            observed_value=1500,
        )

        # Verify violation recorded
        assert violation is not None
        assert violation.observed_value == 1500
        assert violation.limit_value == 1000

        # Verify proposal NOT generated (pattern threshold not met)
        proposal = governor.detect_and_propose_adaptation("memory_limit")
        assert proposal is None

        # Verify boundary unchanged
        boundary = governor.boundaries.get_boundary("memory_limit")
        assert boundary.version == 1


class TestRepeatedViolation:
    """TEST 3: Repeated violations trigger automatic tightening."""

    def test_repeated_violations_tighten(self, governor):
        """Repeated violations generate tighten proposal and auto-approval."""
        # Create boundary and pattern with threshold of 3
        governor.boundaries.create_boundary(
            boundary_id="latency_limit",
            resource_or_action="latency_ms",
            initial_limit=100,
        )
        governor.patterns.create_pattern(
            pattern_id="latency_pattern",
            boundary_id="latency_limit",
            violation_threshold=3,
            time_window_seconds=60,
        )

        # Generate 3 violations
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="latency_limit",
                observed_value=150,
            )
            time.sleep(0.1)  # Small delay to ensure different timestamps

        # Proposal should be generated
        proposal = governor.detect_and_propose_adaptation("latency_limit")
        assert proposal is not None
        assert proposal.direction == AdaptationDirection.TIGHTEN

        # Authorize (should auto-approve)
        approved, result = governor.authorize_proposal(proposal)
        assert result == AuthorizationResult.AUTO_APPROVED
        assert approved.status.value == "approved"

        # Apply adaptation
        new_version = governor.apply_approved_proposal(approved)

        # Verify boundary tightened
        assert new_version.version == 2
        assert new_version.current_limit < 100


class TestLooseningRejected:
    """TEST 4: Loosening proposal cannot be auto-approved."""

    def test_loosen_requires_human_review(self, governor):
        """Loosening adaptation must be rejected by authority."""
        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="io_limit",
            resource_or_action="io_ops",
            initial_limit=1000,
        )

        # Manually create a loosen proposal (simulating a system that tries to loosen)
        proposal = governor.proposals.create_proposal(
            boundary_id="io_limit",
            source_evidence=[],
            current_value=1000,
            proposed_value=2000,
            reason="Testing loosen behavior",
            direction=AdaptationDirection.LOOSEN,
        )

        # Attempt to authorize
        _, result = governor.authorize_proposal(proposal)

        # Must require human review, NOT auto-approve
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW

        # Proposal should still be pending
        updated = governor.proposals.get_proposal(proposal.proposal_id)
        assert updated.status.value == "pending"


class TestDisableRejected:
    """TEST 5: Disabling proposal cannot be auto-approved."""

    def test_disable_requires_human_review(self, governor):
        """Disabling adaptation must be rejected by authority."""
        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="throttle_limit",
            resource_or_action="request_rate",
            initial_limit=100,
        )

        # Manually create a disable proposal
        proposal = governor.proposals.create_proposal(
            boundary_id="throttle_limit",
            source_evidence=[],
            current_value=100,
            proposed_value=None,
            reason="Testing disable behavior",
            direction=AdaptationDirection.DISABLE,
        )

        # Attempt to authorize
        _, result = governor.authorize_proposal(proposal)

        # Must require human review
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW

        # Proposal should remain pending
        updated = governor.proposals.get_proposal(proposal.proposal_id)
        assert updated.status.value == "pending"


class TestGovernanceIntegrity:
    """TEST 10: Governance principles cannot be modified."""

    def test_principles_immutable(self, governor):
        """Principles are immutable and cannot be changed by adaptive mechanism."""
        # Verify system invariants are in place
        assert governor.principles.verify_principle_integrity()

        # Verify that invariants cannot be overwritten
        with pytest.raises(ValueError):
            governor.principles.add_principle(
                GovernancePrinciple(
                    principle_id="inv_001",  # Same as system invariant
                    name="Different",
                    description="Trying to override",
                    principle_type=PrincipleType.AUTHORITY_RULE,
                    statement="Should fail",
                    created_at=time.time(),
                )
            )

        # Verify invariants still intact
        assert governor.principles.verify_principle_integrity()


class TestAuthorityInvariance:
    """Verify authority model cannot be circumvented."""

    def test_tighten_always_auto_approves(self, governor):
        """TIGHTEN operations are always auto-approved."""
        proposal = governor.proposals.create_proposal(
            boundary_id="test",
            source_evidence=[],
            current_value=100,
            proposed_value=90,
            reason="Tightening",
            direction=AdaptationDirection.TIGHTEN,
        )

        result = governor.authority.authorize_proposal(proposal)
        assert result == AuthorizationResult.AUTO_APPROVED

    def test_loosen_never_auto_approves(self, governor):
        """LOOSEN operations never auto-approve."""
        proposal = governor.proposals.create_proposal(
            boundary_id="test",
            source_evidence=[],
            current_value=100,
            proposed_value=200,
            reason="Loosening",
            direction=AdaptationDirection.LOOSEN,
        )

        result = governor.authority.authorize_proposal(proposal)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW

    def test_disable_never_auto_approves(self, governor):
        """DISABLE operations never auto-approve."""
        proposal = governor.proposals.create_proposal(
            boundary_id="test",
            source_evidence=[],
            current_value=100,
            proposed_value=None,
            reason="Disabling",
            direction=AdaptationDirection.DISABLE,
        )

        result = governor.authority.authorize_proposal(proposal)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW
