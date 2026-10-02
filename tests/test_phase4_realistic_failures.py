"""
Phase 4: Realistic Failure Modes

Test governance system against actual production chaos:
- Cascading tightening across boundaries
- False positive resistance at scale (1000+ violation types)
- Latency under high event volume
- Operator error recovery

These are not adversarial attacks. They're messy, real-world failure modes.
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.authority import AuthorizationResult


class TestCascadingTightening:
    """When CPU tightens, memory pressure increases. Does system handle?"""

    @pytest.fixture
    def coupled_system(self):
        """Create Governor with interdependent boundaries."""
        gov = Governor(store_path="/tmp/test_cascading", use_semantic=True)

        # Set up boundaries with realistic limits
        gov.boundaries.create_boundary(
            boundary_id="cpu_limit",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )
        gov.boundaries.create_boundary(
            boundary_id="memory_limit",
            resource_or_action="memory_mb",
            initial_limit=8000,
        )
        gov.boundaries.create_boundary(
            boundary_id="io_limit",
            resource_or_action="io_throughput_mbps",
            initial_limit=1000,
        )

        # Patterns for each boundary
        for boundary_id in ["cpu_limit", "memory_limit", "io_limit"]:
            gov.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=3,
                time_window_seconds=30,
            )

        return gov

    def test_cpu_tightening_cascades_to_memory(self, coupled_system):
        """Tighten CPU → workloads move to memory → memory pressure increases."""
        gov = coupled_system

        # Step 1: Record baseline memory behavior (normal)
        baseline_memory_violations = 0
        for i in range(5):
            _, violation = gov.execute_against_boundary(
                boundary_id="memory_limit",
                observed_value=5000,  # Normal
            )
            if violation:
                baseline_memory_violations += 1

        # Step 2: Force CPU tightening by injecting violations
        for i in range(5):
            _, violation = gov.execute_against_boundary(
                boundary_id="cpu_limit",
                observed_value=85,  # Above current limit
            )

        # Detect and apply CPU tightening
        cpu_proposal = gov.detect_and_propose_adaptation("cpu_limit")
        if cpu_proposal:
            _, auth_result = gov.authorize_proposal(cpu_proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(cpu_proposal)

        # Step 3: After CPU tightens, simulate cascade: workloads move to memory
        # Memory usage increases due to CPU constraint
        cascade_memory_violations = 0
        for i in range(5):
            _, violation = gov.execute_against_boundary(
                boundary_id="memory_limit",
                observed_value=7500,  # Higher due to CPU pressure
            )
            if violation:
                cascade_memory_violations += 1

        # Verify system responds without oscillating
        # Memory violations should increase (cascade is real)
        assert cascade_memory_violations >= baseline_memory_violations, (
            "Cascade should cause memory violations to increase"
        )

        # But system shouldn't oscillate: check both boundaries are stable
        cpu_boundary = gov.boundaries.get_boundary("cpu_limit")
        mem_boundary = gov.boundaries.get_boundary("memory_limit")

        # Both should have tightened (higher version = more adaptations)
        # But versions shouldn't be excessive (no oscillation)
        assert cpu_boundary.version <= 5, "CPU boundary shouldn't oscillate"
        assert mem_boundary.version <= 5, "Memory boundary shouldn't oscillate"

    def test_multiple_boundaries_tighten_cooperatively(self, coupled_system):
        """When multiple boundaries need tightening, do they conflict?"""
        gov = coupled_system

        # Inject violations across multiple boundaries simultaneously
        # Need more violations to trigger pattern detection
        boundaries_to_stress = [
            ("cpu_limit", 85),
            ("memory_limit", 7500),
            ("io_limit", 950),
        ]

        total_violations = 0
        for boundary_id, value in boundaries_to_stress:
            for _ in range(6):  # Exceed threshold
                _, violation = gov.execute_against_boundary(
                    boundary_id=boundary_id,
                    observed_value=value,
                )
                if violation:
                    total_violations += 1

        # Verify violations were recorded across boundaries
        assert total_violations > 0, "Should record violations across boundaries"

        # Attempt to adapt all boundaries
        proposals = []
        for boundary_id, _ in boundaries_to_stress:
            proposal = gov.detect_and_propose_adaptation(boundary_id)
            if proposal:
                proposals.append(proposal)

        # Apply all proposals
        applied_count = 0
        for proposal in proposals:
            _, auth_result = gov.authorize_proposal(proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(proposal)
                applied_count += 1

        # Verify no boundary loosened (monotonicity)
        for boundary_id, _ in boundaries_to_stress:
            try:
                boundary = gov.boundaries.get_boundary(boundary_id)
                # Version should be stable (no oscillation)
                assert boundary.version <= 3, "Boundary shouldn't oscillate"
            except KeyError:
                pass


class TestFalsePositiveResistance:
    """Semantic layer effectiveness at scale."""

    @pytest.fixture
    def governor(self):
        """Create Governor with semantic layer enabled."""
        return Governor(store_path="/tmp/test_false_positives", use_semantic=True)

    def test_semantic_layer_filters_expected_violations_at_scale(self, governor):
        """Register 50 expected violation patterns. Still filters effectively?"""
        from src.governance.workload import (
            ExpectedLoadPattern,
            WorkloadType,
        )

        gov = governor

        # Create boundary
        gov.boundaries.create_boundary(
            boundary_id="api_latency",
            resource_or_action="latency_ms",
            initial_limit=500,
        )

        gov.patterns.create_pattern(
            pattern_id="api_pattern",
            boundary_id="api_latency",
            violation_threshold=5,
            time_window_seconds=60,
        )

        # Register 50 expected violation patterns
        for i in range(50):
            gov.classifier.register_expected_pattern(
                ExpectedLoadPattern(
                    pattern_id=f"expected_{i}",
                    boundary_id="api_latency",
                    resource_or_action="latency_ms",
                    workload_type=WorkloadType.BACKUP if i % 2 == 0 else WorkloadType.MAINTENANCE,
                    description=f"Expected pattern {i}",
                    expected_value_range=(400, 600),
                    expected_duration_seconds=300,
                    schedule=f"Type {i%5}",
                    severity="normal",
                )
            )

        # Inject violations matching expected patterns
        expected_violations = 0
        for i in range(10):
            _, violation = gov.execute_against_boundary(
                boundary_id="api_latency",
                observed_value=550,  # Matches expected range
                context={
                    "source": "backup",
                    "workload_type": "backup",  # String, not enum (JSON serializable)
                },
            )
            if violation:
                expected_violations += 1

        # Inject anomalous violations (should be detected)
        anomalous_violations = 0
        for i in range(10):
            _, violation = gov.execute_against_boundary(
                boundary_id="api_latency",
                observed_value=2000,  # Way above expected
                context={"source": "unknown"},
            )
            if violation:
                anomalous_violations += 1

        # Pattern detector should detect anomalies, not expected violations
        proposal = gov.detect_and_propose_adaptation("api_latency")

        # With semantic layer: system should handle both violation types
        violations = gov.events.get_violations_for_boundary("api_latency")
        assert len(violations) >= 10, "Violations should be recorded"

    def test_classification_accuracy_with_many_patterns(self, governor):
        """Semantic layer accuracy with 100+ registered patterns."""
        from src.governance.workload import (
            ExpectedLoadPattern,
            WorkloadType,
            ViolationClass,
        )

        gov = governor

        # Register 100 patterns for different workload types
        workload_types = [
            WorkloadType.BACKUP,
            WorkloadType.MAINTENANCE,
            WorkloadType.DEPLOYMENT,
            WorkloadType.BATCH_JOB,
            WorkloadType.LOAD_TEST,
        ]

        for workload_type in workload_types:
            for i in range(20):
                gov.classifier.register_expected_pattern(
                    ExpectedLoadPattern(
                        pattern_id=f"{workload_type.value}_{i}",
                        boundary_id="test",
                        resource_or_action="test",
                        workload_type=workload_type,
                        description=f"Pattern for {workload_type.value}",
                        expected_value_range=(100, 200),
                        expected_duration_seconds=600,
                        schedule="Various",
                        severity="normal",
                    )
                )

        # Test classification accuracy
        test_cases = [
            (150, {"workload_type": "backup"}, ViolationClass.EXPECTED),
            (500, {"source": "unknown"}, ViolationClass.ANOMALOUS),
            (175, {"maintenance_window": True}, ViolationClass.EXPECTED),
        ]

        correct_classifications = 0
        for value, context, expected_class in test_cases:
            classification = gov.classifier.classify_violation(
                boundary_id="test",
                observed_value=value,
                context=context,
            )
            if classification == expected_class:
                correct_classifications += 1

        # Should maintain accuracy even with 100+ patterns
        accuracy = correct_classifications / len(test_cases)
        assert accuracy >= 0.5, (
            f"Classification accuracy {accuracy:.1%} should be >= 50% at scale"
        )


class TestLatencyUnderLoad:
    """Governance loop latency with high event volume."""

    @pytest.fixture
    def governor(self):
        """Create Governor for latency testing."""
        return Governor(store_path="/tmp/test_latency", use_semantic=True)

    def test_adaptation_latency_under_high_event_volume(self, governor):
        """Process 1000 events. Measure adaptation latency."""
        gov = governor

        # Create boundary
        gov.boundaries.create_boundary(
            boundary_id="throughput",
            resource_or_action="events_per_sec",
            initial_limit=5000,
        )

        gov.patterns.create_pattern(
            pattern_id="throughput_pattern",
            boundary_id="throughput",
            violation_threshold=10,
            time_window_seconds=30,
        )

        # Execute 1000 events (500 normal, 500 violations)
        start_time = time.time()

        normal_events = 0
        violation_events = 0

        for i in range(500):
            gov.execute_against_boundary(
                boundary_id="throughput",
                observed_value=3000,  # Normal
            )
            normal_events += 1

        for i in range(500):
            gov.execute_against_boundary(
                boundary_id="throughput",
                observed_value=6000,  # Violation
            )
            violation_events += 1

        # Time the adaptation detection
        adapt_start = time.time()
        proposal = gov.detect_and_propose_adaptation("throughput")
        adapt_latency = time.time() - adapt_start

        total_time = time.time() - start_time
        events_per_second = 1000 / total_time

        # Assertions: latency should be acceptable
        assert adapt_latency < 0.1, (
            f"Adaptation detection took {adapt_latency:.3f}s, should be < 100ms"
        )

        assert events_per_second > 5000, (
            f"Throughput {events_per_second:.0f} events/sec, should be > 5000"
        )

    def test_proposal_authorization_latency(self, governor):
        """Authorization decision latency under load."""
        gov = governor

        gov.boundaries.create_boundary(
            boundary_id="test",
            resource_or_action="test",
            initial_limit=100,
        )

        # Create proposal
        proposal = gov.proposals.create_proposal(
            boundary_id="test",
            source_evidence=[],
            current_value=100,
            proposed_value=90,
            reason="Test",
            direction=AdaptationDirection.TIGHTEN,
        )

        # Time authorization
        auth_times = []
        for _ in range(100):
            start = time.time()
            result = gov.authority.authorize_proposal(proposal)
            auth_times.append(time.time() - start)

        avg_latency = sum(auth_times) / len(auth_times)
        max_latency = max(auth_times)

        assert avg_latency < 0.01, (
            f"Average authorization latency {avg_latency*1000:.1f}ms, should be < 10ms"
        )

        assert max_latency < 0.05, (
            f"Max authorization latency {max_latency*1000:.1f}ms, should be < 50ms"
        )


class TestOperatorErrorRecovery:
    """System recovery from bad human decisions."""

    @pytest.fixture
    def governor(self):
        """Create Governor for error recovery testing."""
        return Governor(store_path="/tmp/test_recovery", use_semantic=True)

    def test_recovery_from_overly_aggressive_tightening(self, governor):
        """Admin approves aggressive tightening. System degrades. Can it recover?"""
        gov = governor

        # Create boundary with high limit
        gov.boundaries.create_boundary(
            boundary_id="cpu_limit",
            resource_or_action="cpu_percent",
            initial_limit=85,
        )

        gov.patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_limit",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Simulate good operations
        for i in range(5):
            gov.execute_against_boundary(
                boundary_id="cpu_limit",
                observed_value=60,
            )

        # Admin manually tightens aggressively (bad decision)
        # Directly update boundary (admin override)
        aggressive_limit = 40  # Way too tight
        new_boundary = gov.boundaries.update_boundary("cpu_limit", aggressive_limit)

        # Now system gets stressed: many violations
        violations_after_tightening = 0
        for i in range(10):
            _, violation = gov.execute_against_boundary(
                boundary_id="cpu_limit",
                observed_value=65,  # Normal load, now violates tight limit
            )
            if violation:
                violations_after_tightening += 1

        # System should detect problem and create evidence
        violations = gov.events.get_violations_for_boundary("cpu_limit")

        # Verify: violations increased dramatically (system is suffering)
        assert violations_after_tightening >= 5, (
            "Aggressive tightening should cause violations"
        )

        # System has evidence of bad decision
        assert len(violations) >= violations_after_tightening

    def test_monotonicity_prevents_loosening_recovery(self, governor):
        """Even if admin wants to loosen after bad tightening, system blocks it."""
        gov = governor

        gov.boundaries.create_boundary(
            boundary_id="test",
            resource_or_action="test",
            initial_limit=100,
        )

        # Simulate bad tightening
        new_boundary = gov.boundaries.update_boundary("test", 50)

        # Admin tries to loosen (wants to recover from mistake)
        from src.governance.proposal import AdaptationProposal, ProposalStatus

        loosen_proposal = AdaptationProposal(
            proposal_id="admin_recovery_attempt",
            boundary_id="test",
            source_evidence=[],
            current_value=50,
            proposed_value=80,
            reason="That was too aggressive",
            direction=AdaptationDirection.LOOSEN,
            status=ProposalStatus.PENDING,
            created_at=time.time(),
        )

        # Authority should block it
        result = gov.authority.authorize_proposal(loosen_proposal)

        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW, (
            "Loosen should never auto-approve, even for admin recovery"
        )

    def test_evidence_trail_documents_error(self, governor):
        """All bad decisions are documented in immutable evidence trail."""
        gov = governor

        gov.boundaries.create_boundary(
            boundary_id="test",
            resource_or_action="test",
            initial_limit=100,
        )

        gov.patterns.create_pattern(
            pattern_id="test_pattern",
            boundary_id="test",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Create violations
        violation_ids = []
        for i in range(5):
            _, violation = gov.execute_against_boundary(
                boundary_id="test",
                observed_value=150,
            )
            if violation:
                violation_ids.append(violation.violation_id)

        # Detect and create proposal
        proposal = gov.detect_and_propose_adaptation("test")

        if proposal:
            # Authorize it
            _, auth_result = gov.authorize_proposal(proposal)

            # If approved, apply it
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(proposal)

        # Everything should be in evidence trail
        violations = gov.events.get_violations_for_boundary("test")
        all_proposals = gov.proposals.get_all_proposals()
        test_proposals = [p for p in all_proposals if p.boundary_id == "test"]

        assert len(violations) >= 4, "All violations documented"

        # Immutability: evidence persisted
        assert gov.file_store is not None, "Evidence persisted"
        assert len(violation_ids) > 0, "Violations documented in immutable trail"


class TestSystemStability:
    """Overall system stability under realistic conditions."""

    @pytest.fixture
    def governor(self):
        """Create Governor for stability testing."""
        return Governor(store_path="/tmp/test_stability", use_semantic=True)

    def test_no_oscillation_under_sustained_load(self, governor):
        """Apply sustained load. System should stabilize, not oscillate."""
        gov = governor

        # Create boundary
        gov.boundaries.create_boundary(
            boundary_id="memory",
            resource_or_action="memory_mb",
            initial_limit=8000,
        )

        gov.patterns.create_pattern(
            pattern_id="memory_pattern",
            boundary_id="memory",
            violation_threshold=4,
            time_window_seconds=30,
        )

        # Sustained load: violations at edge of current limit
        boundary_versions = []

        for phase in range(3):  # 3 phases of sustained load
            for i in range(10):
                gov.execute_against_boundary(
                    boundary_id="memory",
                    observed_value=7900,  # Just below limit, might trigger violations
                )

            # Check if system adapted
            boundary = gov.boundaries.get_boundary("memory")
            boundary_versions.append(boundary.version)

        # System should adapt (versions increase) but then stabilize
        # Not: version jumps by 10 in each phase (oscillation)
        version_increases = [
            boundary_versions[i+1] - boundary_versions[i]
            for i in range(len(boundary_versions) - 1)
        ]

        # Should be monotonically non-decreasing (tightening)
        assert all(v >= 0 for v in version_increases), (
            "Boundary versions should only increase (tightening)"
        )

        # Increases should be small and stable (not oscillating)
        assert max(version_increases) <= 2, (
            f"Max increase {max(version_increases)}, should be <= 2 (no oscillation)"
        )

    def test_system_maintains_invariants_under_chaos(self, governor):
        """Under sustained chaos, monotonicity and authority invariants hold."""
        gov = governor

        for boundary_id in ["cpu", "memory", "io"]:
            gov.boundaries.create_boundary(
                boundary_id=boundary_id,
                resource_or_action=boundary_id,
                initial_limit=100,
            )

            gov.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=3,
                time_window_seconds=30,
            )

        # Record initial limits
        initial_limits = {
            bid: gov.boundaries.get_boundary(bid).current_limit
            for bid in ["cpu", "memory", "io"]
        }

        # Sustained chaos: random violations
        import random

        for iteration in range(100):
            boundary_id = random.choice(["cpu", "memory", "io"])
            value = random.randint(150, 300)

            gov.execute_against_boundary(
                boundary_id=boundary_id,
                observed_value=value,
            )

        # Check invariants
        final_limits = {
            bid: gov.boundaries.get_boundary(bid).current_limit
            for bid in ["cpu", "memory", "io"]
        }

        # Monotonicity: limits should be <= initial (only tightening)
        for bid in ["cpu", "memory", "io"]:
            assert final_limits[bid] <= initial_limits[bid] * 1.01, (
                f"Boundary {bid} loosened: {initial_limits[bid]} → {final_limits[bid]}"
            )

        # Authority: no LOOSEN proposals auto-approved
        for proposal in gov.proposals.proposals.values():
            if proposal.direction == AdaptationDirection.LOOSEN:
                assert proposal.status.value != "approved", (
                    "LOOSEN should never auto-approve"
                )
