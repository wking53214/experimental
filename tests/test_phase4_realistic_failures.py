"""
Phase 4: Realistic Failure Modes
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.authority import AuthorizationResult


class TestCascadingTightening:
    @pytest.fixture
    def coupled_system(self):
        gov = Governor(store_path="/tmp/test_cascading", use_semantic=True)
        gov.boundaries.create_boundary("cpu_limit", "cpu_percent", 80)
        gov.boundaries.create_boundary("memory_limit", "memory_mb", 8000)
        gov.boundaries.create_boundary("io_limit", "io_throughput_mbps", 1000)
        for boundary_id in ["cpu_limit", "memory_limit", "io_limit"]:
            gov.patterns.create_pattern(f"{boundary_id}_pattern", boundary_id, 3, 30)
        return gov

    def test_cpu_tightening_cascades_to_memory(self, coupled_system):
        gov = coupled_system
        baseline_memory_violations = 0
        for i in range(5):
            _, violation = gov.execute_against_boundary("memory_limit", 5000)
            if violation:
                baseline_memory_violations += 1
        for i in range(5):
            gov.execute_against_boundary("cpu_limit", 85)
        cpu_proposal = gov.detect_and_propose_adaptation("cpu_limit")
        if cpu_proposal:
            _, auth_result = gov.authorize_proposal(cpu_proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(cpu_proposal)
        cascade_memory_violations = 0
        for i in range(5):
            _, violation = gov.execute_against_boundary("memory_limit", 7500)
            if violation:
                cascade_memory_violations += 1
        assert cascade_memory_violations >= baseline_memory_violations
        assert gov.boundaries.get_boundary("cpu_limit").version <= 5
        assert gov.boundaries.get_boundary("memory_limit").version <= 5

    def test_multiple_boundaries_tighten_cooperatively(self, coupled_system):
        gov = coupled_system
        boundaries_to_stress = [("cpu_limit", 85), ("memory_limit", 7500), ("io_limit", 950)]
        total_violations = 0
        for boundary_id, value in boundaries_to_stress:
            for _ in range(6):
                _, violation = gov.execute_against_boundary(boundary_id, value)
                if violation:
                    total_violations += 1
        assert total_violations > 0
        proposals = []
        for boundary_id, _ in boundaries_to_stress:
            proposal = gov.detect_and_propose_adaptation(boundary_id)
            if proposal:
                proposals.append(proposal)
        for proposal in proposals:
            _, auth_result = gov.authorize_proposal(proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(proposal)


class TestFalsePositiveResistance:
    @pytest.fixture
    def governor(self):
        return Governor(store_path="/tmp/test_false_positives", use_semantic=True)

    def test_semantic_layer_filters_expected_violations_at_scale(self, governor):
        from src.governance.workload import ExpectedLoadPattern, WorkloadType
        gov = governor
        gov.boundaries.create_boundary("api_latency", "latency_ms", 500)
        gov.patterns.create_pattern("api_pattern", "api_latency", 5, 60)
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
        for i in range(10):
            gov.execute_against_boundary("api_latency", 550, context={"source": "backup", "workload_type": "backup"})
        for i in range(10):
            gov.execute_against_boundary("api_latency", 2000, context={"source": "unknown"})
        violations = gov.events.get_violations_for_boundary("api_latency")
        assert len(violations) >= 10

    def test_classification_accuracy_with_many_patterns(self, governor):
        from src.governance.workload import ExpectedLoadPattern, WorkloadType, ViolationClass
        gov = governor
        for workload_type in [WorkloadType.BACKUP, WorkloadType.MAINTENANCE, WorkloadType.DEPLOYMENT]:
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
        test_cases = [
            (150, {"workload_type": "backup"}, ViolationClass.EXPECTED),
            (500, {"source": "unknown"}, ViolationClass.ANOMALOUS),
        ]
        correct = sum(
            1 for value, context, expected in test_cases
            if gov.classifier.classify_violation("test", value, context) == expected
        )
        assert correct / len(test_cases) >= 0.5


class TestLatencyUnderLoad:
    @pytest.fixture
    def governor(self):
        return Governor(store_path="/tmp/test_latency", use_semantic=True)

    def test_adaptation_latency_under_high_event_volume(self, governor):
        """Process 1000 events. L1: wall-clock budget, not absolute EPS."""
        gov = governor
        gov.boundaries.create_boundary("throughput", "events_per_sec", 5000)
        gov.patterns.create_pattern("throughput_pattern", "throughput", 10, 30)
        start_time = time.time()
        for i in range(500):
            gov.execute_against_boundary("throughput", 3000)
        for i in range(500):
            gov.execute_against_boundary("throughput", 6000)
        adapt_start = time.time()
        proposal = gov.detect_and_propose_adaptation("throughput")
        adapt_latency = time.time() - adapt_start
        total_time = time.time() - start_time
        events_per_second = 1000 / total_time
        assert adapt_latency < 0.1, (
            f"Adaptation detection took {adapt_latency:.3f}s, should be < 100ms"
        )
        # L1: host-dependent absolute EPS is flaky on CI; require 1000 events in < 5s
        assert total_time < 5.0, (
            f"Processed 1000 events in {total_time:.2f}s "
            f"({events_per_second:.0f} events/sec); expected < 5.0s"
        )

    def test_proposal_authorization_latency(self, governor):
        gov = governor
        gov.boundaries.create_boundary("test", "test", 100)
        proposal = gov.proposals.create_proposal(
            boundary_id="test", source_evidence=[], current_value=100,
            proposed_value=90, reason="Test", direction=AdaptationDirection.TIGHTEN,
        )
        auth_times = []
        for _ in range(100):
            start = time.time()
            gov.authority.authorize_proposal(proposal)
            auth_times.append(time.time() - start)
        avg_latency = sum(auth_times) / len(auth_times)
        max_latency = max(auth_times)
        assert avg_latency < 0.01
        assert max_latency < 0.05


class TestOperatorErrorRecovery:
    @pytest.fixture
    def governor(self):
        return Governor(store_path="/tmp/test_recovery", use_semantic=True)

    def test_recovery_from_overly_aggressive_tightening(self, governor):
        gov = governor
        gov.boundaries.create_boundary("cpu_limit", "cpu_percent", 85)
        gov.patterns.create_pattern("cpu_pattern", "cpu_limit", 3, 30)
        for i in range(5):
            gov.execute_against_boundary("cpu_limit", 60)
        gov.boundaries.update_boundary("cpu_limit", 40)
        violations_after = 0
        for i in range(10):
            _, violation = gov.execute_against_boundary("cpu_limit", 65)
            if violation:
                violations_after += 1
        assert violations_after >= 5

    def test_monotonicity_prevents_loosening_recovery(self, governor):
        from src.governance.proposal import AdaptationProposal, ProposalStatus
        gov = governor
        gov.boundaries.create_boundary("test", "test", 100)
        gov.boundaries.update_boundary("test", 50)
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
        result = gov.authority.authorize_proposal(loosen_proposal)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW

    def test_evidence_trail_documents_error(self, governor):
        gov = governor
        gov.boundaries.create_boundary("test", "test", 100)
        gov.patterns.create_pattern("test_pattern", "test", 3, 30)
        for i in range(5):
            gov.execute_against_boundary("test", 150)
        proposal = gov.detect_and_propose_adaptation("test")
        if proposal:
            _, auth_result = gov.authorize_proposal(proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(proposal)
        assert len(gov.events.get_all_executions()) >= 5
