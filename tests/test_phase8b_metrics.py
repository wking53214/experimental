"""
Phase 8B: Metric Collection Infrastructure Tests

Test metric stream abstraction, baseline establishment, and correlation analysis
for multi-metric attack detection and end-to-end governance workflows.
"""

import time
from src.governance.metrics import (
    MetricDatapoint,
    MetricStream,
    BaselineEstablisher,
    CorrelationAnalyzer,
    MetricsTracker,
)


class TestMetricDatapoint:
    """Test individual metric datapoint."""

    def test_create_datapoint(self):
        """Test creating a datapoint."""
        dp = MetricDatapoint(timestamp=1000.0, boundary_id="test_boundary")

        assert dp.timestamp == 1000.0
        assert dp.boundary_id == "test_boundary"
        assert dp.get_all_metrics() == {}

    def test_set_and_get_metric(self):
        """Test setting and retrieving metrics."""
        dp = MetricDatapoint(1000.0, "test")

        dp.set_metric("error_rate", 0.05)
        dp.set_metric("throughput", 1000.0)

        assert dp.get_metric("error_rate") == 0.05
        assert dp.get_metric("throughput") == 1000.0
        assert dp.get_metric("nonexistent") is None

    def test_get_all_metrics(self):
        """Test retrieving all metrics."""
        dp = MetricDatapoint(1000.0, "test")

        dp.set_metric("error_rate", 0.05)
        dp.set_metric("throughput", 1000.0)

        all_metrics = dp.get_all_metrics()
        assert all_metrics == {"error_rate": 0.05, "throughput": 1000.0}

        # Returned dict is copy, not reference
        all_metrics["error_rate"] = 0.5
        assert dp.get_metric("error_rate") == 0.05


class TestMetricStream:
    """Test metric stream time-series ingestion."""

    def test_create_stream(self):
        """Test creating a metric stream."""
        stream = MetricStream("test_boundary", history_window=50)

        assert stream.boundary_id == "test_boundary"
        assert stream.history_window == 50
        assert stream.size() == 0

    def test_add_observation(self):
        """Test adding observations to stream."""
        stream = MetricStream("test", history_window=100)

        dp = stream.add_observation(1000.0, {"error_rate": 0.05, "throughput": 1000.0})

        assert dp.timestamp == 1000.0
        assert dp.boundary_id == "test"
        assert stream.size() == 1

    def test_add_multiple_observations(self):
        """Test adding multiple observations."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"error_rate": 0.05, "throughput": 1000.0})
        stream.add_observation(1001.0, {"error_rate": 0.06, "throughput": 900.0})
        stream.add_observation(1002.0, {"error_rate": 0.07, "throughput": 800.0})

        assert stream.size() == 3

    def test_history_window_trimming(self):
        """Test that history is trimmed to window size."""
        stream = MetricStream("test", history_window=5)

        for i in range(10):
            stream.add_observation(1000.0 + i, {"value": float(i)})

        assert stream.size() == 5

        # Oldest should be 5, newest 9
        history = stream.get_history()
        assert history[0].metrics["value"] == 5.0
        assert history[-1].metrics["value"] == 9.0

    def test_get_history_by_metric(self):
        """Test retrieving history for specific metric."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"error_rate": 0.05, "throughput": 1000.0})
        stream.add_observation(1001.0, {"error_rate": 0.06, "throughput": 900.0})
        stream.add_observation(1002.0, {"error_rate": 0.07, "throughput": 800.0})

        error_history = stream.get_history("error_rate")

        assert len(error_history) == 3
        assert error_history[0] == (1000.0, 0.05)
        assert error_history[1] == (1001.0, 0.06)
        assert error_history[2] == (1002.0, 0.07)

    def test_get_history_all_datapoints(self):
        """Test retrieving all datapoints."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"error_rate": 0.05})
        stream.add_observation(1001.0, {"error_rate": 0.06})

        history = stream.get_history()

        assert len(history) == 2
        assert history[0].timestamp == 1000.0
        assert history[1].timestamp == 1001.0

    def test_get_latest_observation(self):
        """Test retrieving latest observation."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"error_rate": 0.05, "throughput": 1000.0})
        stream.add_observation(1001.0, {"error_rate": 0.06, "throughput": 900.0})

        latest = stream.get_latest()
        assert latest[0] == 1001.0
        assert latest[1] == {"error_rate": 0.06, "throughput": 900.0}

    def test_get_latest_metric(self):
        """Test retrieving latest value for specific metric."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"error_rate": 0.05, "throughput": 1000.0})
        stream.add_observation(1001.0, {"error_rate": 0.06, "throughput": 900.0})

        latest = stream.get_latest("error_rate")
        assert latest == (1001.0, 0.06)

    def test_get_metric_names(self):
        """Test retrieving all metric names."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"error_rate": 0.05, "throughput": 1000.0})
        stream.add_observation(1001.0, {"error_rate": 0.06, "latency": 50.0})

        names = stream.get_metric_names()
        assert names == {"error_rate", "throughput", "latency"}

    def test_get_time_range(self):
        """Test retrieving time range."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"value": 1.0})
        stream.add_observation(2000.0, {"value": 2.0})

        time_range = stream.get_time_range()
        assert time_range == (1000.0, 2000.0)

    def test_empty_stream_operations(self):
        """Test operations on empty stream."""
        stream = MetricStream("test", history_window=100)

        assert stream.size() == 0
        assert stream.get_history() == []
        assert stream.get_latest() is None
        assert stream.get_metric_names() == set()
        assert stream.get_time_range() is None


class TestBaselineEstablisher:
    """Test baseline learning and management."""

    def test_create_baseline_establisher(self):
        """Test creating baseline establisher."""
        establisher = BaselineEstablisher(min_observations=5, learning_buffer_size=20)

        assert establisher.min_observations == 5
        assert establisher.learning_buffer_size == 20
        assert not establisher.is_established()

    def test_learn_baseline_from_stream(self):
        """Test learning baseline from metric stream."""
        stream = MetricStream("test", history_window=100)

        # Add 15 observations with stable error rate (0.05)
        for i in range(15):
            stream.add_observation(1000.0 + i, {"error_rate": 0.05})

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        assert "error_rate" in baseline
        assert 0.04 < baseline["error_rate"]["mean"] < 0.06  # Close to 0.05
        assert baseline["error_rate"]["count"] >= 10

    def test_baseline_with_multiple_metrics(self):
        """Test baseline with multiple metrics."""
        stream = MetricStream("test", history_window=100)

        for i in range(15):
            stream.add_observation(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
                "latency": 50.0,
            })

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        assert "error_rate" in baseline
        assert "throughput" in baseline
        assert "latency" in baseline

    def test_baseline_percentiles(self):
        """Test that baseline includes percentiles."""
        stream = MetricStream("test", history_window=100)

        for i in range(20):
            stream.add_observation(1000.0 + i, {"value": float(i)})

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        assert "percentile_5" in baseline["value"]
        assert "percentile_95" in baseline["value"]
        assert baseline["value"]["percentile_5"] <= baseline["value"]["mean"]
        assert baseline["value"]["percentile_95"] >= baseline["value"]["mean"]

    def test_insufficient_observations(self):
        """Test that baseline fails with insufficient observations."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"value": 1.0})
        stream.add_observation(1001.0, {"value": 2.0})

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        assert baseline == {}
        assert not establisher.is_established()

    def test_is_established_check(self):
        """Test checking if baseline is established."""
        stream = MetricStream("test", history_window=100)

        for i in range(15):
            stream.add_observation(1000.0 + i, {"error_rate": 0.05})

        establisher = BaselineEstablisher(min_observations=10)

        assert not establisher.is_established("error_rate")
        establisher.learn_baseline(stream)
        assert establisher.is_established("error_rate")
        assert establisher.is_established()  # Any metric

    def test_get_baseline(self):
        """Test retrieving specific metric baseline."""
        stream = MetricStream("test", history_window=100)

        for i in range(15):
            stream.add_observation(1000.0 + i, {"error_rate": 0.05})

        establisher = BaselineEstablisher(min_observations=10)
        establisher.learn_baseline(stream)

        baseline = establisher.get_baseline("error_rate")
        assert baseline is not None
        assert "mean" in baseline

    def test_refresh_baseline(self):
        """Test refreshing baseline with new data."""
        stream = MetricStream("test", history_window=100)

        for i in range(15):
            stream.add_observation(1000.0 + i, {"value": float(i)})

        establisher = BaselineEstablisher(min_observations=10)
        updated = establisher.refresh_baseline(stream)

        assert updated is True

    def test_startup_transient_handling(self):
        """Test that startup transients don't skew baseline."""
        stream = MetricStream("test", history_window=100)

        # Add some startup transients (high values)
        for i in range(3):
            stream.add_observation(1000.0 + i, {"value": 100.0})

        # Then stable values
        for i in range(15):
            stream.add_observation(1003.0 + i, {"value": 10.0})

        establisher = BaselineEstablisher(min_observations=10, learning_buffer_size=50)
        baseline = establisher.learn_baseline(stream)

        # Baseline should be close to 10, not influenced by startup spike to 100
        assert baseline["value"]["mean"] < 50  # Should be closer to 10


class TestCorrelationAnalyzer:
    """Test multi-metric attack detection."""

    def test_create_analyzer(self):
        """Test creating correlation analyzer."""
        analyzer = CorrelationAnalyzer(correlation_threshold=0.7)

        assert analyzer.correlation_threshold == 0.7

    def test_normal_metrics_no_gaming(self):
        """Test that normal metrics don't trigger gaming detection."""
        stream = MetricStream("test", history_window=100)

        for i in range(15):
            stream.add_observation(1000.0 + i, {
                "violation_rate": 0.05,
                "error_rate": 0.02,
                "throughput": 1000.0,
                "latency_p99": 50.0,
            })

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        analyzer = CorrelationAnalyzer()
        results = analyzer.analyze_stream(stream, baseline)

        assert not results["gaming_detected"]
        assert results["pareto_gaming_score"] < 0.5

    def test_pareto_gaming_detection(self):
        """Test detection of Pareto gaming attack."""
        stream = MetricStream("test", history_window=100)

        # Establish baseline with normal metrics
        for i in range(15):
            stream.add_observation(1000.0 + i, {
                "violation_rate": 0.05,
                "error_rate": 0.02,
                "throughput": 1000.0,
            })

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        # Simulate Pareto gaming: violations improve but throughput collapses
        stream.add_observation(1015.0, {
            "violation_rate": 0.02,  # Improved (lower is better)
            "error_rate": 0.01,  # Improved
            "throughput": 500.0,  # Collapsed (half of baseline)
        })

        analyzer = CorrelationAnalyzer()
        results = analyzer.analyze_stream(stream, baseline)

        assert results["gaming_detected"]
        assert results["pareto_gaming_score"] > 0.3

    def test_multi_metric_anomaly_detection(self):
        """Test detection of coordinated multi-metric anomalies."""
        stream = MetricStream("test", history_window=100)

        # Establish baseline
        for i in range(15):
            stream.add_observation(1000.0 + i, {
                "metric_a": 100.0,
                "metric_b": 200.0,
                "metric_c": 50.0,
                "metric_d": 75.0,
            })

        establisher = BaselineEstablisher(min_observations=10)
        baseline = establisher.learn_baseline(stream)

        # Add observation with multiple large deviations
        stream.add_observation(1015.0, {
            "metric_a": 250.0,  # 2.5x baseline, >2 std devs
            "metric_b": 50.0,   # 0.25x baseline, >2 std devs
            "metric_c": 45.0,   # Normal
            "metric_d": 200.0,  # 2.67x baseline, >2 std devs
        })

        analyzer = CorrelationAnalyzer()
        results = analyzer.analyze_stream(stream, baseline)

        assert len(results["anomalies"]) > 0
        assert results["anomalies"][0]["type"] == "multi_metric_anomaly"

    def test_analyze_empty_stream(self):
        """Test analyzing empty stream."""
        stream = MetricStream("test", history_window=100)
        baseline = {}

        analyzer = CorrelationAnalyzer()
        results = analyzer.analyze_stream(stream, baseline)

        assert not results["gaming_detected"]
        assert results["anomalies"] == []

    def test_analyze_insufficient_baseline(self):
        """Test analyzing with insufficient baseline."""
        stream = MetricStream("test", history_window=100)
        stream.add_observation(1000.0, {"value": 1.0})

        baseline = {}  # Empty baseline

        analyzer = CorrelationAnalyzer()
        results = analyzer.analyze_stream(stream, baseline)

        assert not results["gaming_detected"]


class TestMetricStreamIntegration:
    """Integration tests for metric stream workflow."""

    def test_complete_baseline_and_detection_workflow(self):
        """Test complete workflow: stream -> baseline -> detection."""
        # Create stream and add training data
        stream = MetricStream("boundary_1", history_window=100)

        # Add normal training data
        for i in range(20):
            stream.add_observation(1000.0 + i, {
                "violation_rate": 0.05,
                "error_rate": 0.02,
                "throughput": 1000.0,
                "latency": 50.0,
            })

        # Establish baseline
        establisher = BaselineEstablisher(min_observations=15)
        baseline = establisher.learn_baseline(stream)

        assert establisher.is_established()
        assert len(baseline) == 4

        # Add test observation (normal)
        stream.add_observation(1020.0, {
            "violation_rate": 0.05,
            "error_rate": 0.02,
            "throughput": 1000.0,
            "latency": 50.0,
        })

        # Analyze
        analyzer = CorrelationAnalyzer()
        results = analyzer.analyze_stream(stream, baseline)

        assert not results["gaming_detected"]
        assert results["pareto_gaming_score"] == 0.0

    def test_multi_boundary_streams(self):
        """Test managing streams for multiple boundaries."""
        streams = {
            "boundary_1": MetricStream("boundary_1", history_window=100),
            "boundary_2": MetricStream("boundary_2", history_window=100),
        }

        # Add observations to both
        for i in range(15):
            for boundary_id, stream in streams.items():
                stream.add_observation(1000.0 + i, {
                    "error_rate": 0.05 + (0.01 if boundary_id == "boundary_2" else 0),
                    "throughput": 1000.0,
                })

        # Both should have observations
        assert streams["boundary_1"].size() == 15
        assert streams["boundary_2"].size() == 15

        # Boundaries should be independent
        assert streams["boundary_1"].boundary_id == "boundary_1"
        assert streams["boundary_2"].boundary_id == "boundary_2"

    def test_streaming_with_metrics_tracker(self):
        """Test integration with existing MetricsTracker."""
        tracker = MetricsTracker()
        stream = MetricStream("test", history_window=100)

        # Add training data to stream
        for i in range(15):
            stream.add_observation(1000.0 + i, {
                "violation_rate": 0.05,
                "throughput": 1000.0,
            })

        # Register pre-adaptation metrics with tracker
        pre_metrics = stream.get_latest()[1]  # Get latest metrics
        tracker.evaluate_proposal(
            proposal_id="prop_1",
            boundary_id="test",
            pre_metrics=pre_metrics,
        )

        # Simulate post-adaptation
        stream.add_observation(1015.0, {
            "violation_rate": 0.03,  # Improved
            "throughput": 1000.0,  # Maintained
        })

        post_metrics = stream.get_latest()[1]
        outcome, confidence = tracker.evaluate_post_adaptation(
            proposal_id="prop_1",
            boundary_id="test",
            post_metrics=post_metrics,
        )

        # Should show improvement
        assert outcome.value in ["improved", "unchanged"]
        assert confidence > 0


class TestMetricStreamPerformance:
    """Performance and edge case tests."""

    def test_large_history(self):
        """Test handling large metric histories."""
        stream = MetricStream("test", history_window=1000)

        # Add 1000 observations
        for i in range(1000):
            stream.add_observation(1000.0 + i, {
                "metric_1": float(i % 100),
                "metric_2": float(i % 50),
            })

        assert stream.size() == 1000

        # Should still be able to retrieve history
        history = stream.get_history("metric_1")
        assert len(history) == 1000

    def test_many_metrics_per_observation(self):
        """Test handling many metrics per observation."""
        stream = MetricStream("test", history_window=100)

        # Create 50 metrics per observation
        metrics = {f"metric_{i}": float(i) for i in range(50)}
        stream.add_observation(1000.0, metrics)

        assert stream.get_metric_names() == set(metrics.keys())
        assert stream.size() == 1

    def test_sparse_metric_data(self):
        """Test handling sparse data (missing metrics in some observations)."""
        stream = MetricStream("test", history_window=100)

        # Some observations have different metrics
        stream.add_observation(1000.0, {"metric_a": 1.0, "metric_b": 2.0})
        stream.add_observation(1001.0, {"metric_c": 3.0})
        stream.add_observation(1002.0, {"metric_a": 1.5, "metric_c": 3.5})

        names = stream.get_metric_names()
        assert "metric_a" in names
        assert "metric_b" in names
        assert "metric_c" in names

        # Historical retrieval handles sparse data
        history_a = stream.get_history("metric_a")
        assert len(history_a) == 2  # Only observations with metric_a

    def test_zero_and_negative_metrics(self):
        """Test handling zero and negative metric values."""
        stream = MetricStream("test", history_window=100)

        stream.add_observation(1000.0, {"metric": 0.0})
        stream.add_observation(1001.0, {"metric": -5.0})
        stream.add_observation(1002.0, {"metric": 10.0})

        establisher = BaselineEstablisher(min_observations=1)
        baseline = establisher.learn_baseline(stream)

        assert "metric" in baseline
        assert baseline["metric"]["min"] == -5.0
        assert baseline["metric"]["max"] == 10.0
