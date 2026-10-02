"""Necessity proofs: semantic layer and metrics are required for correct governance."""
import time
import pytest
from src.governance.governor import Governor
from src.governance.workload import ExpectedLoadPattern, WorkloadType


class TestNecessityOfSemanticLayer:
    def test_without_semantic_false_positives(self):
        """Without semantic layer: expected load triggers unnecessary tightening."""
        governor = Governor(
            store_path=f"/tmp/test_necessity_no_semantic_{int(time.time()*1000)}",
            use_semantic=False,
        )
        governor.boundaries.create_boundary(
            boundary_id="cpu_limit", resource_or_action="cpu_percent", initial_limit=80,
        )
        governor.patterns.create_pattern(
            pattern_id="cpu_pattern", boundary_id="cpu_limit",
            violation_threshold=3, time_window_seconds=30,
        )
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="cpu_limit", observed_value=85,
                context={"workload_type": "database_migration", "expected": True},
            )
            time.sleep(0.05)
        proposal = governor.detect_and_propose_adaptation("cpu_limit")
        assert proposal is not None, "Should generate proposal (false positive)"
        assert proposal.direction.value == "tighten"

    def test_with_semantic_expected_violations_filtered(self):
        """With semantic layer: expected violations are filtered."""
        governor = Governor(
            store_path=f"/tmp/test_necessity_semantic_v2_{int(time.time()*1000)}",
            use_semantic=True,
        )
        governor.boundaries.create_boundary(
            boundary_id="cpu_limit", resource_or_action="cpu_percent", initial_limit=80,
        )
        governor.patterns.create_pattern(
            pattern_id="cpu_pattern", boundary_id="cpu_limit",
            violation_threshold=3, time_window_seconds=30,
        )
        governor.classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="migration",
                boundary_id="cpu_limit",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.BATCH_JOB,
                description="Database migration",
                expected_value_range=(80, 90),
                expected_duration_seconds=3600,
                schedule="On-demand",
                severity="elevated",
            )
        )
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="cpu_limit", observed_value=85,
                context={"workload_type": "database_migration", "expected": True},
            )
            time.sleep(0.05)
        proposal = governor.detect_and_propose_adaptation("cpu_limit")
        assert proposal is None, "Should NOT generate proposal (expected violation filtered)"


class TestNecessityOfMetrics:
    def test_metrics_enable_effectiveness_tracking(self):
        governor = Governor(store_path=f"/tmp/test_necessity_metrics_{int(time.time()*1000)}")
        governor.boundaries.create_boundary("cpu", "cpu", 100)
        assert governor.metrics_tracker is not None
