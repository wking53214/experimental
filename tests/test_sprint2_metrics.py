"""
Phase 2 Sprint 2: Metrics Collection & Effectiveness Tests

Test data-driven validation: track before/after metrics to determine if
adaptations actually improve the system.

Key achievement: Replace categorical UNKNOWN with measured outcomes.
"""
import pytest
import time
from src.governance.metrics import (
    MetricsCollector, EffectivenessOracle, MetricsTracker,
    MetricSnapshot, AdaptationMetrics, EffectivenessOutcome
)
from src.governance.governor import Governor


class TestMetricsCollection:
    """Test basic metrics collection."""

    def test_record_pre_adaptation_snapshot(self):
        """Collector records pre-adaptation metrics."""
        collector = MetricsCollector()

        metrics = {
            "violation_rate": 0.15,
            "slo_attainment": 0.95,
            "throughput": 1000.0,
        }

        snapshot = collector.record_pre_adaptation_snapshot(
            boundary_id="test_boundary",
            metrics=metrics,
        )

        assert snapshot.boundary_id == "test_boundary"
        assert snapshot.get_metric("violation_rate") == 0.15
        assert snapshot.get_metric("slo_attainment") == 0.95

    def test_record_post_adaptation_snapshot(self):
        """Collector records post-adaptation metrics and creates metrics record."""
        collector = MetricsCollector()

        # Record pre-adaptation
        pre_metrics = {
            "violation_rate": 0.15,
            "slo_attainment": 0.95,
            "throughput": 1000.0,
        }
        collector.record_pre_adaptation_snapshot("test", pre_metrics)

        # Record post-adaptation (after waiting)
        time.sleep(0.1)
        post_metrics = {
            "violation_rate": 0.08,  # Improved
            "slo_attainment": 0.97,  # Improved
            "throughput": 1050.0,    # Improved
        }

        adaptation_metrics = collector.record_post_adaptation_snapshot(
            proposal_id="proposal_1",
            boundary_id="test",
            metrics=post_metrics,
            observation_duration_seconds=10,
        )

        assert adaptation_metrics.proposal_id == "proposal_1"
        assert adaptation_metrics.snapshot_post.get_metric("violation_rate") == 0.08


class TestEffectivenessEvaluation:
    """Test effectiveness measurement."""

    def test_evaluate_improved_outcome(self):
        """Effectiveness oracle detects improved outcomes."""
        oracle = EffectivenessOracle()

        # Create adaptation metrics showing improvement
        snapshot_pre = MetricSnapshot(
            timestamp=time.time() - 300,
            boundary_id="test",
            metrics={
                "violation_rate": 0.15,
                "slo_attainment": 0.95,
                "throughput": 1000.0,
            }
        )

        snapshot_post = MetricSnapshot(
            timestamp=time.time(),
            boundary_id="test",
            metrics={
                "violation_rate": 0.08,  # 46% improvement
                "slo_attainment": 0.97,  # 2% improvement
                "throughput": 1050.0,    # 5% improvement
            }
        )

        adaptation_metrics = AdaptationMetrics(
            proposal_id="prop_1",
            boundary_id="test",
            snapshot_pre=snapshot_pre,
            snapshot_post=snapshot_post,
            observation_duration_seconds=300,
        )

        outcome, confidence, reasoning = oracle.evaluate_adaptation(adaptation_metrics)

        assert outcome == EffectivenessOutcome.IMPROVED
        assert confidence > 0.5
        assert "Improved" in reasoning

    def test_evaluate_degraded_outcome(self):
        """Effectiveness oracle detects degraded outcomes."""
        oracle = EffectivenessOracle()

        snapshot_pre = MetricSnapshot(
            timestamp=time.time() - 300,
            boundary_id="test",
            metrics={
                "violation_rate": 0.15,
                "slo_attainment": 0.95,
                "throughput": 1000.0,
            }
        )

        snapshot_post = MetricSnapshot(
            timestamp=time.time(),
            boundary_id="test",
            metrics={
                "violation_rate": 0.25,  # 67% worse
                "slo_attainment": 0.85,  # 11% worse
                "throughput": 800.0,     # 20% worse
            }
        )

        adaptation_metrics = AdaptationMetrics(
            proposal_id="prop_2",
            boundary_id="test",
            snapshot_pre=snapshot_pre,
            snapshot_post=snapshot_post,
            observation_duration_seconds=300,
        )

        outcome, confidence, reasoning = oracle.evaluate_adaptation(adaptation_metrics)

        assert outcome == EffectivenessOutcome.DEGRADED
        assert confidence > 0.5
        assert "Degraded" in reasoning

    def test_evaluate_unchanged_outcome(self):
        """Effectiveness oracle detects unchanged outcomes."""
        oracle = EffectivenessOracle()

        snapshot_pre = MetricSnapshot(
            timestamp=time.time() - 300,
            boundary_id="test",
            metrics={
                "violation_rate": 0.15,
                "slo_attainment": 0.95,
                "throughput": 1000.0,
            }
        )

        snapshot_post = MetricSnapshot(
            timestamp=time.time(),
            boundary_id="test",
            metrics={
                "violation_rate": 0.14,  # 7% improvement (below threshold)
                "slo_attainment": 0.954,  # 0.4% improvement
                "throughput": 1010.0,    # 1% improvement
            }
        )

        adaptation_metrics = AdaptationMetrics(
            proposal_id="prop_3",
            boundary_id="test",
            snapshot_pre=snapshot_pre,
            snapshot_post=snapshot_post,
            observation_duration_seconds=300,
        )

        outcome, confidence, reasoning = oracle.evaluate_adaptation(adaptation_metrics)

        assert outcome == EffectivenessOutcome.UNCHANGED
        assert "Unchanged" in reasoning


class TestMetricsTracker:
    """Test integrated metrics tracking."""

    def test_full_evaluation_cycle(self):
        """Track a full pre/post adaptation evaluation cycle."""
        tracker = MetricsTracker()

        # Pre-adaptation
        pre_metrics = {
            "violation_rate": 0.20,
            "slo_attainment": 0.90,
            "throughput": 900.0,
            "error_rate": 0.05,
        }

        tracker.evaluate_proposal("prop_1", "boundary_1", pre_metrics)

        # Wait and measure post-adaptation
        time.sleep(0.1)
        post_metrics = {
            "violation_rate": 0.10,  # 50% improvement
            "slo_attainment": 0.95,  # 5.6% improvement
            "throughput": 950.0,     # 5.6% improvement
            "error_rate": 0.03,      # 40% improvement
        }

        outcome, confidence = tracker.evaluate_post_adaptation(
            "prop_1",
            "boundary_1",
            post_metrics,
            observation_duration_seconds=10,
        )

        assert outcome == EffectivenessOutcome.IMPROVED
        assert confidence > 0.5

    def test_effectiveness_summary(self):
        """Track effectiveness summary across multiple adaptations."""
        tracker = MetricsTracker()

        # Simulate 3 adaptations with different outcomes
        scenarios = [
            # Improved
            (
                "prop_1",
                {
                    "violation_rate": 0.20,
                    "slo_attainment": 0.90,
                    "throughput": 900.0,
                },
                {
                    "violation_rate": 0.10,
                    "slo_attainment": 0.95,
                    "throughput": 950.0,
                },
            ),
            # Unchanged
            (
                "prop_2",
                {
                    "violation_rate": 0.15,
                    "slo_attainment": 0.92,
                    "throughput": 920.0,
                },
                {
                    "violation_rate": 0.14,
                    "slo_attainment": 0.924,
                    "throughput": 930.0,
                },
            ),
            # Degraded
            (
                "prop_3",
                {
                    "violation_rate": 0.12,
                    "slo_attainment": 0.94,
                    "throughput": 940.0,
                },
                {
                    "violation_rate": 0.22,
                    "slo_attainment": 0.84,
                    "throughput": 800.0,
                },
            ),
        ]

        for proposal_id, pre_metrics, post_metrics in scenarios:
            tracker.evaluate_proposal(proposal_id, "boundary_1", pre_metrics)
            time.sleep(0.05)
            tracker.evaluate_post_adaptation(
                proposal_id,
                "boundary_1",
                post_metrics,
                observation_duration_seconds=10,
            )

        # Get summary
        summary = tracker.get_boundary_effectiveness_summary("boundary_1")

        assert summary["total"] == 3
        assert summary["improved"] == 1
        assert summary["unchanged"] == 1
        assert summary["degraded"] == 1
        assert summary["success_rate"] == pytest.approx(2/3, rel=0.01)


class TestGovernorMetricsIntegration:
    """Test Governor with metrics tracking."""

    def test_governor_with_metrics(self):
        """Governor tracks metrics for proposals."""
        governor = Governor(
            store_path=f"/tmp/test_gov_metrics_{int(time.time()*1000)}",
            use_semantic=False
        )

        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="monitored_resource",
            resource_or_action="cpu_percent",
            initial_limit=80,
        )

        # Create pattern detector
        governor.patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="monitored_resource",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Record pre-adaptation metrics
        pre_metrics = {
            "violation_rate": 0.10,
            "slo_attainment": 0.95,
            "throughput": 1000.0,
        }
        governor.metrics_tracker.evaluate_proposal(
            "test_proposal",
            "monitored_resource",
            pre_metrics,
        )

        # Generate violations
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="monitored_resource",
                observed_value=95,
            )
            time.sleep(0.1)

        # Check proposal generated
        proposal = governor.detect_and_propose_adaptation("monitored_resource")
        assert proposal is not None

        # Measure post-adaptation
        time.sleep(0.1)
        post_metrics = {
            "violation_rate": 0.05,  # Improved
            "slo_attainment": 0.97,
            "throughput": 1100.0,
        }

        outcome, confidence = governor.metrics_tracker.evaluate_post_adaptation(
            proposal.proposal_id,
            "monitored_resource",
            post_metrics,
            observation_duration_seconds=10,
        )

        assert outcome == EffectivenessOutcome.IMPROVED


class TestMetricsBoundaryScenarios:
    """Test metrics at boundary conditions."""

    def test_zero_pre_metric_handling(self):
        """Oracle handles zero values in pre-adaptation metrics."""
        oracle = EffectivenessOracle()

        snapshot_pre = MetricSnapshot(
            timestamp=time.time() - 300,
            boundary_id="test",
            metrics={
                "violation_rate": 0.0,  # Zero metric
                "throughput": 1000.0,
            }
        )

        snapshot_post = MetricSnapshot(
            timestamp=time.time(),
            boundary_id="test",
            metrics={
                "violation_rate": 0.05,
                "throughput": 900.0,
            }
        )

        adaptation_metrics = AdaptationMetrics(
            proposal_id="prop",
            boundary_id="test",
            snapshot_pre=snapshot_pre,
            snapshot_post=snapshot_post,
            observation_duration_seconds=300,
        )

        outcome, confidence, reasoning = oracle.evaluate_adaptation(adaptation_metrics)

        # Should handle gracefully without division error
        assert outcome is not None
        assert "throughput" in reasoning  # At least one metric evaluated

    def test_missing_metrics_inconclusive(self):
        """Oracle returns inconclusive when metrics missing."""
        oracle = EffectivenessOracle()

        snapshot_pre = MetricSnapshot(
            timestamp=time.time() - 300,
            boundary_id="test",
            metrics={}
        )

        snapshot_post = MetricSnapshot(
            timestamp=time.time(),
            boundary_id="test",
            metrics={}
        )

        adaptation_metrics = AdaptationMetrics(
            proposal_id="prop",
            boundary_id="test",
            snapshot_pre=snapshot_pre,
            snapshot_post=snapshot_post,
            observation_duration_seconds=300,
        )

        outcome, confidence, reasoning = oracle.evaluate_adaptation(adaptation_metrics)

        assert outcome == EffectivenessOutcome.INCONCLUSIVE
        assert confidence == 0.0
