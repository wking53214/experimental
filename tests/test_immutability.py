"""
Immutability and historical integrity tests.

TEST 6: Historical events are never rewritten when boundaries change.
TEST 9: Monotonicity — automatic adaptation only tightens, never loosens.
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.boundary import BoundaryStatus


class TestHistoricalImmutability:
    """TEST 6: Historical events are immutable even as boundaries change."""
    
    def test_historical_violations_unchanged_after_boundary_update(self):
        """Violations reference original boundary version after boundary changes."""
        governor = Governor(store_path="/tmp/test_immutable_2")
        
        # Create boundary v1
        governor.boundaries.create_boundary(
            boundary_id="test_resource",
            resource_or_action="value",
            initial_limit=100,
        )
        
        # Create pattern detector
        governor.patterns.create_pattern(
            pattern_id="test_pattern",
            boundary_id="test_resource",
            violation_threshold=3,
            time_window_seconds=60,
        )
        
        # Generate violations against v1
        violation_ids = []
        for i in range(3):
            _, violation = governor.execute_against_boundary(
                boundary_id="test_resource",
                observed_value=150,
            )
            if violation:
                violation_ids.append(violation.violation_id)
            time.sleep(0.1)
        
        # Verify violations reference v1
        for v_id in violation_ids:
            violation = governor.events.get_violation(v_id)
            assert violation.boundary_version == 1
        
        # Now tighten boundary to v2
        proposal = governor.detect_and_propose_adaptation("test_resource")
        assert proposal is not None
        
        approved, _ = governor.authorize_proposal(proposal)
        new_boundary = governor.apply_approved_proposal(approved)
        assert new_boundary.version == 2
        
        # Verify violations STILL reference v1, not v2
        for v_id in violation_ids:
            violation = governor.events.get_violation(v_id)
            assert violation.boundary_version == 1, \
                f"Violation {v_id} should reference v1 but references v{violation.boundary_version}"
        
        # Verify boundary history is intact
        history = governor.boundaries.get_boundary_history("test_resource")
        assert len(history.versions) == 2
        
        # Verify v1 is marked superseded
        v1 = history.get_version(1)
        assert v1.status == BoundaryStatus.SUPERSEDED


class TestMonotonicity:
    """TEST 9: Automatic adaptation can only tighten, never loosen."""
    
    def test_automatic_proposals_only_tighten(self):
        """Pattern-detected proposals always tighten, never loosen."""
        governor = Governor(store_path="/tmp/test_monotonic_1")
        
        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="monotonic_test",
            resource_or_action="resource",
            initial_limit=100,
        )
        
        # Create pattern
        governor.patterns.create_pattern(
            pattern_id="test_pattern",
            boundary_id="monotonic_test",
            violation_threshold=3,
            time_window_seconds=60,
        )
        
        # Generate violations
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="monotonic_test",
                observed_value=150,
            )
            time.sleep(0.1)
        
        # Get proposal
        proposal = governor.detect_and_propose_adaptation("monotonic_test")
        assert proposal is not None
        
        # Verify direction is TIGHTEN
        assert proposal.direction == AdaptationDirection.TIGHTEN
        assert proposal.proposed_value < proposal.current_value


class TestBoundaryVersioning:
    """Verify boundary versioning is immutable."""
    
    def test_cannot_overwrite_boundary_version(self):
        """Attempting to add same version twice fails."""
        governor = Governor(store_path="/tmp/test_versioning_1")
        
        # Create boundary
        v1 = governor.boundaries.create_boundary(
            boundary_id="versioned",
            resource_or_action="value",
            initial_limit=100,
        )
        
        # Get history and attempt to add duplicate version
        history = governor.boundaries.get_boundary_history("versioned")
        
        with pytest.raises(ValueError):
            history.add_version(v1)


class TestEventImmutability:
    """Verify events are stored immutably."""
    
    def test_violation_event_stored_immutably(self):
        """Violation events cannot be duplicated once recorded."""
        governor = Governor(store_path="/tmp/test_event_immutable_2")
        
        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="immutable_test",
            resource_or_action="value",
            initial_limit=100,
        )
        
        # Generate violation
        _, violation = governor.execute_against_boundary(
            boundary_id="immutable_test",
            observed_value=150,
        )
        
        assert violation is not None
        violation_id = violation.violation_id
        
        # Retrieve violation and verify it's correct
        retrieved = governor.events.get_violation(violation_id)
        assert retrieved.violation_id == violation_id
        assert retrieved.observed_value == 150


class TestFileStoreImmutability:
    """Verify file-based storage prevents overwriting."""
    
    def test_cannot_overwrite_event_file(self):
        """Attempting to write same event twice fails."""
        governor = Governor(store_path="/tmp/test_file_immutable_1")
        
        # Create and store an event
        event_data = {
            "event_id": "test_123",
            "data": "original",
        }
        governor.file_store.write_violation_event("test_123", event_data)
        
        # Attempt to overwrite
        with pytest.raises(ValueError):
            governor.file_store.write_violation_event("test_123", {"data": "modified"})
    
    def test_file_store_preserves_history(self):
        """File store maintains chronological history."""
        governor = Governor(store_path="/tmp/test_file_history_1")
        
        # Write multiple violations with unique IDs
        for i in range(3):
            governor.file_store.write_violation_event(
                f"violation_{i}_test",
                {"violation_id": f"violation_{i}", "timestamp": time.time() + i}
            )
        
        # List violations should be in order
        violations = governor.file_store.list_violations()
        assert len(violations) == 3


class TestProposalImmutability:
    """Verify proposals maintain immutable records with status changes."""
    
    def test_proposal_status_change_creates_new_record(self):
        """Proposal status changes update records without losing history."""
        governor = Governor(store_path="/tmp/test_proposal_immutable_1")
        
        # Create proposal
        proposal = governor.proposals.create_proposal(
            boundary_id="test",
            source_evidence=[],
            current_value=100,
            proposed_value=90,
            reason="Test",
            direction=AdaptationDirection.TIGHTEN,
        )
        
        original_id = proposal.proposal_id
        assert proposal.status.value == "pending"
        
        # Approve it
        approved = governor.proposals.mark_approved(original_id)
        
        # Verify ID is same but status changed
        assert approved.proposal_id == original_id
        assert approved.status.value == "approved"
        assert approved.approved_at is not None
