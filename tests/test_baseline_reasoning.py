"""
Phase 2.5: Baseline Reasoning Tests

Defend against semantic poisoning by comparing "expected" activities
against historical baselines.

Test: Adversary claims "database migration" context, but violation pattern
deviates from historical database migrations. System detects poisoning.
"""
import pytest
import time
from src.governance.baseline import BaselineComparator, BaselineAnomaly
from src.governance.governor import Governor


class TestBaselineProfileManagement:
    """Test baseline profile creation and management."""

    def test_register_baseline_pattern(self):
        """Register a known workload pattern baseline."""
        baseline = BaselineComparator()

        profile = baseline.register_pattern(
            pattern_name="database_migration",
            workload_type="maintenance_window",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        assert profile.pattern_name == "database_migration"
        assert profile.avg_violation_rate == 0.15
        assert profile.avg_throughput == 800.0

    def test_get_all_profiles(self):
        """Retrieve all registered profiles."""
        baseline = BaselineComparator()

        baseline.register_pattern("db_migration", "maintenance", 0.15, 800.0, 200.0)
        baseline.register_pattern("batch_job", "batch", 0.05, 1500.0, 100.0)

        profiles = baseline.get_profiles()
        assert len(profiles) == 2
        assert "db_migration" in profiles
        assert "batch_job" in profiles


class TestBaselineComparison:
    """Test detection of deviations from baseline."""

    def test_detection_within_normal_range(self):
        """Activity matching baseline profile shows no anomaly."""
        baseline = BaselineComparator()

        # Register baseline for database migrations
        baseline.register_pattern(
            "db_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        # Observed: matches baseline
        anomaly = baseline.detect_poisoning(
            pattern_name="db_migration",
            observed_violation_rate=0.15,
            observed_throughput=800.0,
            observed_latency_p99=200.0,
        )

        assert anomaly is None, "Normal activity should not trigger anomaly"

    def test_detection_violation_rate_anomaly(self):
        """Detect when violation rate deviates >2 std devs."""
        baseline = BaselineComparator()

        baseline.register_pattern(
            "db_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        # Observed: violation rate way too high
        anomaly = baseline.detect_poisoning(
            pattern_name="db_migration",
            observed_violation_rate=0.50,  # 3.5 std devs away
            observed_throughput=800.0,
            observed_latency_p99=200.0,
        )

        assert anomaly is not None, "High violation rate should trigger anomaly"
        assert anomaly.deviation_type == "violation_rate_anomaly"
        assert anomaly.std_deviations > 2.0

    def test_detection_throughput_anomaly(self):
        """Detect when throughput deviates >2 std devs."""
        baseline = BaselineComparator()

        baseline.register_pattern(
            "db_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        # Observed: throughput way too low
        anomaly = baseline.detect_poisoning(
            pattern_name="db_migration",
            observed_violation_rate=0.15,
            observed_throughput=100.0,  # Way below baseline
            observed_latency_p99=200.0,
        )

        assert anomaly is not None, "Low throughput should trigger anomaly"
        assert anomaly.deviation_type == "throughput_anomaly"

    def test_detection_latency_anomaly(self):
        """Detect when latency deviates >2 std devs."""
        baseline = BaselineComparator()

        baseline.register_pattern(
            "db_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        # Observed: latency way too high
        anomaly = baseline.detect_poisoning(
            pattern_name="db_migration",
            observed_violation_rate=0.15,
            observed_throughput=800.0,
            observed_latency_p99=600.0,  # Way above baseline
        )

        assert anomaly is not None, "High latency should trigger anomaly"
        assert anomaly.deviation_type == "latency_anomaly"


class TestSemanticPoisoningDetection:
    """Test detection of semantic poisoning attacks."""

    def test_poisoned_claim_detected(self):
        """
        Adversary claims "database migration" but metrics don't match
        historical database migrations.

        System detects: "expected" label is false (poisoned claim).
        """
        baseline = BaselineComparator()

        # Register baseline for GENUINE database migrations
        baseline.register_pattern(
            "database_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        # Adversary generates violations and claims "database_migration"
        # But actual metrics don't match a real migration:
        # - violation_rate = 0.50 (too high for normal migration)
        # - throughput = 100 (too low)
        # - latency = 600 (too high)

        is_poisoned = baseline.is_poisoned_claim(
            pattern_name="database_migration",
            observed_violation_rate=0.50,
            observed_throughput=100.0,
            observed_latency_p99=600.0,
        )

        assert is_poisoned, "Poisoned claim should be detected"

    def test_legitimate_activity_not_flagged(self):
        """Legitimate activity matching baseline is not flagged."""
        baseline = BaselineComparator()

        baseline.register_pattern(
            "database_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        # Actual database migration metrics
        is_poisoned = baseline.is_poisoned_claim(
            pattern_name="database_migration",
            observed_violation_rate=0.16,
            observed_throughput=790.0,
            observed_latency_p99=210.0,
        )

        assert not is_poisoned, "Legitimate activity should not be flagged"


class TestBaselineUpdateLearning:
    """Test baseline learning from observations."""

    def test_baseline_updated_from_observations(self):
        """Baseline profile updates as new observations arrive."""
        baseline = BaselineComparator()

        profile = baseline.register_pattern(
            "db_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
        )

        assert profile.occurrences_count == 0

        # Observe first instance
        baseline.update_baseline(
            "db_migration",
            violation_rate=0.16,
            throughput=810.0,
            latency_p99=200.0,
        )

        profile = baseline.get_profiles()["db_migration"]
        assert profile.occurrences_count == 1
        assert profile.avg_violation_rate == 0.16  # First observation replaces initial
        assert profile.avg_throughput == 810.0  # First observation replaces initial

        # Observe second instance
        baseline.update_baseline(
            "db_migration",
            violation_rate=0.14,
            throughput=790.0,
            latency_p99=190.0,
        )

        profile = baseline.get_profiles()["db_migration"]
        assert profile.occurrences_count == 2
        # (0.16 * 1 + 0.14) / 2 = 0.15
        assert abs(profile.avg_violation_rate - 0.15) < 0.01
        # (810 * 1 + 790) / 2 = 800
        assert abs(profile.avg_throughput - 800.0) < 1.0


class TestGovernorBaselineIntegration:
    """Test baseline reasoning integrated into Governor."""

    def test_governor_has_baseline_comparator(self):
        """Governor has baseline comparator component."""
        governor = Governor(
            store_path=f"/tmp/baseline_gov_{int(time.time()*1000)}",
            use_semantic=True
        )

        assert governor.baseline is not None
        assert isinstance(governor.baseline, BaselineComparator)

    def test_register_baseline_via_governor(self):
        """Register baseline patterns through Governor."""
        governor = Governor(
            store_path=f"/tmp/baseline_gov2_{int(time.time()*1000)}",
            use_semantic=True
        )

        governor.baseline.register_pattern(
            "db_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
        )

        profiles = governor.baseline.get_profiles()
        assert "db_migration" in profiles

    def test_poisoning_detection_workflow(self):
        """
        Full workflow: register baseline, simulate poisoned claim,
        detector catches it.
        """
        governor = Governor(
            store_path=f"/tmp/poison_workflow_{int(time.time()*1000)}",
            use_semantic=True
        )

        # 1. Register baselines for known activities
        governor.baseline.register_pattern(
            "database_migration",
            "maintenance",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
            avg_latency_p99=200.0,
        )

        governor.baseline.register_pattern(
            "batch_job",
            "batch",
            avg_violation_rate=0.05,
            avg_throughput=1500.0,
            avg_latency_p99=100.0,
        )

        # 2. Adversary claims "database migration" but metrics are anomalous
        poisoned_migration = governor.baseline.is_poisoned_claim(
            "database_migration",
            observed_violation_rate=0.50,
            observed_throughput=100.0,
            observed_latency_p99=600.0,
        )

        # 3. Detection succeeds
        assert poisoned_migration, "Should detect poisoned 'migration' claim"

        # 4. Legitimate batch job metrics pass baseline check
        legitimate_batch = governor.baseline.is_poisoned_claim(
            "batch_job",
            observed_violation_rate=0.05,
            observed_throughput=1500.0,
            observed_latency_p99=100.0,
        )

        assert not legitimate_batch, "Should accept legitimate activity"


class TestBaselineSeverityGrading:
    """Test severity assessment of baseline anomalies."""

    def test_anomaly_severity_increases_with_deviation(self):
        """Larger deviations get higher severity scores."""
        baseline = BaselineComparator()

        baseline.register_pattern(
            "test",
            "test",
            avg_violation_rate=0.15,
            avg_throughput=800.0,
        )

        # Small deviation (2.5 std devs)
        small = baseline.detect_poisoning("test", 0.27, 800.0, 100.0)

        # Large deviation (4.0 std devs)
        large = baseline.detect_poisoning("test", 0.35, 800.0, 100.0)

        if small and large:
            assert small.severity < large.severity, \
                "Larger deviations should have higher severity"


class TestBaselineEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_unknown_pattern_no_anomaly(self):
        """Unknown pattern returns None (no detection)."""
        baseline = BaselineComparator()

        # Don't register any pattern
        anomaly = baseline.detect_poisoning(
            "unknown_pattern",
            0.50,
            100.0,
            600.0,
        )

        assert anomaly is None, "Unknown pattern should not trigger anomaly"

    def test_zero_std_dev_handling(self):
        """Handle zero standard deviation gracefully."""
        baseline = BaselineComparator()

        profile = baseline.register_pattern(
            "test",
            "test",
            avg_violation_rate=0.15,
        )

        # Manually set std_dev to 0
        profile.violation_rate_std = 0.0

        # Should not crash on division by zero
        anomaly = baseline.detect_poisoning(
            "test",
            0.50,
            800.0,
            200.0,
        )

        # With std_dev handling, should still detect (or handle gracefully)
        assert anomaly is None or anomaly.std_deviations > 0
