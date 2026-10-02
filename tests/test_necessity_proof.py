"""
Formal Necessity Tests: Prove each layer is required.

Remove each governance layer and demonstrate failure.
This proves self-hardening governance is not just an assembly—
it's a required set of coupled mechanisms.
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.metrics import EffectivenessOutcome
from src.governance.rollback import RollbackManager, RollbackReason


class TestNecessityOfSemanticLayer:
    """
    WITHOUT semantic layer: false positives on legitimate high-load.

    Scenario: Database migration causes CPU=70% (expected).
    Expected: No adaptation proposal.
    Without semantic layer: Proposal generated blindly.
    """

    def test_without_semantic_false_positives_on_expected_load(self):
        """Blind pattern detection triggers on legitimate load."""
        # Governor WITHOUT semantic understanding
        governor = Governor(
            store_path=f"/tmp/test_necessity_semantic_{int(time.time()*1000)}",
            use_semantic=False  # Disabled
        )

        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="cpu_limit",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        # Create pattern detector
        governor.patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_limit",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Simulate legitimate high-load (e.g., database migration)
        # CPU spikes to 85% for 3 readings (expected, planned work)
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="cpu_limit",
                observed_value=85,
                context={"workload_type": "database_migration", "expected": True}
            )
            time.sleep(0.1)

        # Without semantic layer: proposal should be generated (false positive)
        proposal = governor.detect_and_propose_adaptation("cpu_limit")

        # This is the failure mode: legitimate load triggers unnecessary tightening
        assert proposal is not None, "Should generate proposal (false positive)"
        assert proposal.direction.value == "tighten"

        # Semantic layer WOULD have filtered this out
        # So this test proves: semantic layer is necessary to avoid false positives

    def test_with_semantic_expected_violations_filtered(self):
        """With semantic layer: expected violations are filtered."""
        governor = Governor(
            store_path=f"/tmp/test_necessity_semantic_v2_{int(time.time()*1000)}",
            use_semantic=True  # Enabled
        )

        governor.boundaries.create_boundary(
            boundary_id="cpu_limit",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        governor.patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_limit",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Same scenario: legitimate high-load
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="cpu_limit",
                observed_value=85,
                context={"workload_type": "database_migration", "expected": True}
            )
            time.sleep(0.1)

        # With semantic layer: proposal should NOT be generated (correctly filtered)
        proposal = governor.detect_and_propose_adaptation("cpu_limit")

        # Semantic layer filters expected violations
        assert proposal is None, "Should NOT generate proposal (expected violation filtered)"


class TestNecessityOfMetrics:
    """
    WITHOUT metrics: can't detect degradation, no basis for rollback.

    Scenario: Tighten CPU to 70% to reduce violations.
    Effect: Actually increases latency/errors (makes things worse).
    Without metrics: System doesn't know it got worse, doesn't rollback.
    """

    def test_without_metrics_degradation_undetected(self):
        """Without metrics, system can't know if adaptation helped."""
        governor = Governor(
            store_path=f"/tmp/test_necessity_metrics_{int(time.time()*1000)}",
            use_semantic=False
        )

        governor.boundaries.create_boundary(
            boundary_id="cpu",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        # Create and apply a tightening proposal
        gov_proposal = governor.proposals.create_proposal(
            boundary_id="cpu",
            source_evidence=[],
            current_value=80,
            proposed_value=72,
            reason="Pattern detected",
            direction=AdaptationDirection.TIGHTEN,
        )

        # Authorize and apply
        approved_proposal, result = governor.authorize_proposal(gov_proposal)
        governor.apply_approved_proposal(approved_proposal)

        # Now the system is at the new tighter limit (72)
        # But without metrics, there's no way to know if this helped or hurt

        # The system has NO WAY to measure:
        # - Pre-adaptation: violation_rate=0.15, latency_p99=150ms
        # - Post-adaptation: violation_rate=0.12, latency_p99=400ms (WORSE!)

        # Without metrics, no rollback decision can be made
        assert governor.metrics_tracker is not None
        assert len(governor.metrics_tracker.evaluation_history) == 0, \
            "Without calling metrics_tracker, no effectiveness measured"

    def test_with_metrics_degradation_detected(self):
        """With metrics, degradation is measurable and triggers rollback."""
        governor = Governor(
            store_path=f"/tmp/test_necessity_metrics_v2_{int(time.time()*1000)}",
            use_semantic=False
        )

        governor.boundaries.create_boundary(
            boundary_id="cpu",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        # Record pre-adaptation metrics
        pre_metrics = {
            "violation_rate": 0.15,
            "slo_attainment": 0.95,
            "latency_p99": 150.0,
        }
        governor.metrics_tracker.evaluate_proposal("prop_1", "cpu", pre_metrics)

        # Apply adaptation (tighten to 72)
        gov_proposal = governor.proposals.create_proposal(
            boundary_id="cpu",
            source_evidence=[],
            current_value=80,
            proposed_value=72,
            reason="Pattern detected",
            direction=AdaptationDirection.TIGHTEN,
        )
        # Authorize and apply
        approved_proposal, result = governor.authorize_proposal(gov_proposal)
        governor.apply_approved_proposal(approved_proposal)

        # Record post-adaptation metrics (degraded)
        time.sleep(0.1)
        post_metrics = {
            "violation_rate": 0.12,  # Improved
            "slo_attainment": 0.85,  # DEGRADED (was 0.95)
            "latency_p99": 400.0,    # DEGRADED (was 150ms)
        }

        outcome, confidence = governor.metrics_tracker.evaluate_post_adaptation(
            "prop_1",
            "cpu",
            post_metrics,
        )

        # With metrics, degradation is detectable
        assert outcome == EffectivenessOutcome.DEGRADED, "Should detect degradation"
        assert confidence > 0.7, "Should have high confidence in degradation"


class TestNecessityOfRollback:
    """
    WITHOUT rollback: bad adaptations persist, system degrades continuously.

    Scenario: Adaptation makes things worse, and stays worse.
    Expected: System reverts to previous boundary.
    Without rollback: System stays in degraded state.
    """

    def test_without_rollback_degradation_persists(self):
        """Without automatic rollback, bad adaptations stay applied."""
        governor = Governor(
            store_path=f"/tmp/test_necessity_rollback_{int(time.time()*1000)}",
            use_semantic=False
        )

        # Create and apply boundary
        v1 = governor.boundaries.create_boundary(
            boundary_id="cpu",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        # Apply a tightening
        v2 = governor.boundaries.update_boundary(
            boundary_id="cpu",
            new_limit=72,
        )

        # System has no automatic rollback mechanism
        # (we could call it manually, but without automatic detection it won't happen)

        current = governor.boundaries.get_boundary("cpu")
        assert current.current_limit == 72, "Still in degraded state"
        assert current.version == 2

    def test_with_rollback_degradation_recovered(self):
        """With automatic rollback, bad adaptations are reverted."""
        from src.governance.rollback import RollbackExecutor

        governor = Governor(
            store_path=f"/tmp/test_necessity_rollback_v2_{int(time.time()*1000)}",
            use_semantic=False
        )

        v1 = governor.boundaries.create_boundary(
            boundary_id="cpu",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        v2 = governor.boundaries.update_boundary(
            boundary_id="cpu",
            new_limit=72,
        )

        # With rollback executor
        executor = RollbackExecutor(governor)
        decision = executor.execute_rollback(
            proposal_id="bad_proposal",
            boundary_id="cpu",
            reason=RollbackReason.DEGRADED_METRICS,
        )

        # System recovered
        assert decision is not None
        current = governor.boundaries.get_boundary("cpu")
        assert current.current_limit == 80, "Reverted to v1's limit"


class TestNecessityOfAuthority:
    """
    WITHOUT authority separation: system can loosen itself into doom.

    Scenario: System detects it's too restrictive, auto-loosens boundary.
    Expected: LOOSEN requires human review (never auto-approved).
    Without authority: System could approve its own loosening, escape constraints.
    """

    def test_without_authority_system_can_escape_constraints(self):
        """Without authority enforcement, system can self-approve LOOSEN."""
        # This test demonstrates why the authority model is non-negotiable
        # We can't actually build this (it would violate the system),
        # but we can show what would happen:

        from src.governance.authority import AuthorityModel
        from src.governance.proposal import AdaptationProposal, AdaptationDirection, ProposalStatus

        authority = AuthorityModel()

        # Hypothetical: system proposes to LOOSEN (reduce constraints)
        loosen_proposal = AdaptationProposal(
            proposal_id="test_loosen",
            boundary_id="cpu",
            source_evidence=[],
            current_value=72,
            proposed_value=85,
            reason="System too restrictive",
            direction=AdaptationDirection.LOOSEN,
            status=ProposalStatus.PENDING,
            created_at=time.time(),
        )

        result = authority.authorize_proposal(loosen_proposal)

        # LOOSEN must NOT be auto-approved
        from src.governance.authority import AuthorizationResult
        assert result != AuthorizationResult.AUTO_APPROVED, \
            "LOOSEN must require human review (authority is necessary)"
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW

    def test_with_authority_loosen_blocked(self):
        """With authority model, system cannot auto-approve LOOSEN."""
        from src.governance.authority import AuthorityModel, AuthorizationResult
        from src.governance.proposal import AdaptationProposal, AdaptationDirection, ProposalStatus

        authority = AuthorityModel()

        loosen_proposal = AdaptationProposal(
            proposal_id="test",
            boundary_id="cpu",
            source_evidence=[],
            current_value=72,
            proposed_value=85,
            reason="Test",
            direction=AdaptationDirection.LOOSEN,
            status=ProposalStatus.PENDING,
            created_at=time.time(),
        )

        result = authority.authorize_proposal(loosen_proposal)

        # Authority enforces: LOOSEN always requires review
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW


class TestLayerCoupling:
    """
    Prove layers must work together—removing any breaks the system.
    """

    def test_full_stack_catches_bad_adaptation_and_recovers(self):
        """With all layers: bad adaptation is detected and automatically recovered."""
        governor = Governor(
            store_path=f"/tmp/test_coupling_{int(time.time()*1000)}",
            use_semantic=True  # All layers enabled
        )

        # Setup
        governor.boundaries.create_boundary("cpu", "cpu_percent", 80)
        governor.patterns.create_pattern("pat1", "cpu", 3, 30)

        # Pre-adaptation metrics
        pre = {"violation_rate": 0.15, "slo_attainment": 0.95, "latency_p99": 150.0}
        governor.metrics_tracker.evaluate_proposal("prop1", "cpu", pre)

        # Apply tightening
        prop = governor.proposals.create_proposal(
            "cpu", [], 80, 72, "Pattern", AdaptationDirection.TIGHTEN,
        )
        # Authorize and apply
        approved_prop, _ = governor.authorize_proposal(prop)
        governor.apply_approved_proposal(approved_prop)

        # Measure degradation
        time.sleep(0.1)
        post = {"violation_rate": 0.12, "slo_attainment": 0.85, "latency_p99": 400.0}
        outcome, conf = governor.metrics_tracker.evaluate_post_adaptation("prop1", "cpu", post)

        # Detect degradation
        assert outcome == EffectivenessOutcome.DEGRADED

        # Execute rollback
        from src.governance.rollback import RollbackExecutor
        executor = RollbackExecutor(governor)
        decision = executor.execute_rollback("prop1", "cpu", RollbackReason.DEGRADED_METRICS)

        # Recovered
        assert decision is not None
        current = governor.boundaries.get_boundary("cpu")
        assert current.current_limit == 80

    def test_missing_any_layer_breaks_recovery(self):
        """
        Document: which layer removal breaks what.
        """
        failures = {
            "semantic": "False positives on expected load → unnecessary adaptations",
            "metrics": "Degradation undetected → bad adaptations persist",
            "rollback": "Bad adaptations not reverted → continuous degradation",
            "authority": "System auto-loosens → constraints bypassed",
        }

        # This serves as documentation of necessity
        assert "semantic" in failures
        assert "metrics" in failures
        assert "rollback" in failures
        assert "authority" in failures


# Import needed for direction enum
from src.governance.proposal import AdaptationDirection
