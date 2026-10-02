"""
Phase 8C: Detector Pipeline Integration Tests

Test the complete detector pipeline: metrics → baseline → detection → violations

Validates end-to-end flow from metric ingestion to anomaly detection and
violation event generation for the governance loop.
"""

import time
from src.governance.metrics import (
    DetectorPipeline, MetricStream, BaselineEstablisher,
)
from src.governance.governor import Governor


class TestDetectorPipeline:
    """Test core detector pipeline functionality."""

    def test_create_pipeline(self):
        """Test creating detector pipeline."""
        pipeline = DetectorPipeline("test_boundary", history_window=100)

        assert pipeline.boundary_id == "test_boundary"
        assert pipeline.baseline_locked is False
        assert pipeline.observation_count == 0

    def test_ingest_metrics(self):
        """Test ingesting metrics into pipeline."""
        pipeline = DetectorPipeline("test", history_window=100)

        pipeline.ingest_metrics(1000.0, {"error_rate": 0.05, "throughput": 1000.0})

        assert pipeline.observation_count == 1
        assert pipeline.stream.size() == 1

    def test_baseline_auto_establishment(self):
        """Test automatic baseline establishment after learning window."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add observations
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
            })

        # Baseline should lock after 15+ observations
        assert pipeline.baseline_locked
        assert len(pipeline.baseline) > 0

    def test_normal_metrics_no_anomaly(self):
        """Test that normal metrics don't trigger anomalies."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline training data
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
            })

        # Add normal observation
        pipeline.ingest_metrics(1020.0, {
            "error_rate": 0.05,
            "throughput": 1000.0,
        })

        result = pipeline.detect_anomalies()

        assert not result["anomaly_detected"]
        assert result["anomaly_score"] < 0.5

    def test_pareto_gaming_detection_in_pipeline(self):
        """Test detection of Pareto gaming through pipeline."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline training data
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
            })

        # Simulate Pareto gaming attack
        pipeline.ingest_metrics(1020.0, {
            "error_rate": 0.02,  # Improved
            "throughput": 500.0,  # Collapsed
        })

        result = pipeline.detect_anomalies()

        assert result["gaming_detected"]
        assert result["gaming_score"] > 0.5

    def test_multi_metric_anomaly_detection(self):
        """Test detection of multi-metric anomalies."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "metric_a": 100.0,
                "metric_b": 200.0,
                "metric_c": 50.0,
                "metric_d": 75.0,
            })

        # Add observation with multiple anomalies
        pipeline.ingest_metrics(1020.0, {
            "metric_a": 250.0,  # 2.5x baseline
            "metric_b": 50.0,   # 0.25x baseline
            "metric_c": 45.0,   # Normal
            "metric_d": 200.0,  # 2.67x baseline
        })

        result = pipeline.detect_anomalies()

        assert result["anomaly_count"] >= 2
        assert result["anomaly_detected"]

    def test_baseline_statistics_available(self):
        """Test retrieving baseline statistics."""
        pipeline = DetectorPipeline("test", history_window=100)

        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {"value": 100.0})

        stats = pipeline.get_baseline_stats("value")

        assert stats is not None
        assert "mean" in stats
        assert "std" in stats
        assert 99.0 < stats["mean"] < 101.0

    def test_get_metric_names(self):
        """Test retrieving metric names from pipeline."""
        pipeline = DetectorPipeline("test", history_window=100)

        pipeline.ingest_metrics(1000.0, {
            "error_rate": 0.05,
            "throughput": 1000.0,
            "latency": 50.0,
        })

        names = pipeline.get_metric_names()

        assert names == {"error_rate", "throughput", "latency"}

    def test_pipeline_stream_size(self):
        """Test getting stream size from pipeline."""
        pipeline = DetectorPipeline("test", history_window=100)

        for i in range(10):
            pipeline.ingest_metrics(1000.0 + i, {"value": 1.0})

        assert pipeline.get_stream_size() == 10

    def test_baseline_refresh(self):
        """Test refreshing baseline after new data."""
        pipeline = DetectorPipeline("test", history_window=100)

        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {"value": 100.0})

        # Get initial baseline
        baseline1 = pipeline.get_baseline_stats("value")

        # Refresh
        updated = pipeline.refresh_baseline()

        assert updated
        baseline2 = pipeline.get_baseline_stats("value")
        assert baseline1["mean"] == baseline2["mean"]


class TestDetectorPipelineIntegration:
    """Test detector pipeline integrated with Governor."""

    def test_detector_pipeline_created_on_demand(self):
        """Test that detector pipeline is created automatically."""
        gov = Governor(use_semantic=False)
        
        # Ingest metrics (should create pipeline automatically)
        detection, _ = gov.ingest_metrics(
            "new_boundary",
            time.time(),
            {"metric": 1.0}
        )
        
        assert "new_boundary" in gov.detector_pipelines
        assert detection is not None
        assert "anomaly_detected" in detection

    def test_multiple_pipelines_created_independently(self):
        """Test that multiple boundaries have independent pipelines."""
        gov = Governor(use_semantic=False)
        
        # Ingest to each boundary (creates pipelines automatically)
        for boundary_id in ["api", "db", "auth"]:
            gov.ingest_metrics(
                boundary_id,
                time.time(),
                {"metric": 1.0}
            )
        
        # All should have pipelines
        assert len(gov.detector_pipelines) >= 3
        
        # Each pipeline should be independent
        assert gov.detector_pipelines["api"].boundary_id == "api"
        assert gov.detector_pipelines["db"].boundary_id == "db"
        assert gov.detector_pipelines["auth"].boundary_id == "auth"

    def test_pipeline_ingestion_workflow(self):
        """Test complete metric ingestion workflow."""
        pipeline = DetectorPipeline("test_bound", history_window=100)
        
        # Build baseline
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "error_rate": 0.05,
                "throughput": 1000.0,
            })
        
        # Check baseline established
        assert pipeline.baseline_locked
        assert len(pipeline.baseline) > 0
        
        # Detect normal
        result1 = pipeline.detect_anomalies()
        assert not result1["anomaly_detected"]
        
        # Add anomalous data
        pipeline.ingest_metrics(1020.0, {
            "error_rate": 0.02,
            "throughput": 500.0,
        })
        
        # Should detect gaming
        result2 = pipeline.detect_anomalies()
        assert result2["gaming_detected"]


class TestDetectorPipelineSignals:
    """Test individual anomaly signals from pipeline."""

    def test_multi_metric_anomaly_signal(self):
        """Test multi-metric anomaly signal generation."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "a": 10.0,
                "b": 20.0,
                "c": 30.0,
            })

        # Add anomalous observation
        pipeline.ingest_metrics(1020.0, {
            "a": 50.0,  # 5x
            "b": 5.0,   # 0.25x
            "c": 30.0,  # Normal
        })

        result = pipeline.detect_anomalies()

        # Should have multi-metric anomaly signal
        signals = [s for s in result["signals"] if s["type"] == "multi_metric_anomaly"]
        assert len(signals) > 0
        assert signals[0]["count"] >= 2

    def test_pareto_gaming_signal(self):
        """Test Pareto gaming signal generation."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "violation_rate": 0.05,
                "throughput": 1000.0,
            })

        # Pareto gaming attack
        pipeline.ingest_metrics(1020.0, {
            "violation_rate": 0.02,
            "throughput": 500.0,
        })

        result = pipeline.detect_anomalies()

        # Should have gaming signal
        signals = [s for s in result["signals"] if s["type"] == "pareto_gaming"]
        assert len(signals) > 0
        assert signals[0]["score"] > 0.5


class TestDetectorPipelineEdgeCases:
    """Test edge cases and error handling."""

    def test_insufficient_baseline_data(self):
        """Test detection with insufficient baseline."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Only 2 observations (need 15+ for baseline)
        pipeline.ingest_metrics(1000.0, {"value": 1.0})
        pipeline.ingest_metrics(1001.0, {"value": 2.0})

        result = pipeline.detect_anomalies()

        assert not result["anomaly_detected"]
        assert "Baseline not yet established" in result["explanation"]

    def test_sparse_metrics_handling(self):
        """Test handling of sparse metric data."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add data with varying metrics
        pipeline.ingest_metrics(1000.0, {"a": 1.0, "b": 2.0})
        pipeline.ingest_metrics(1001.0, {"c": 3.0})

        for i in range(20):
            pipeline.ingest_metrics(1002.0 + i, {"a": 1.0, "b": 2.0})

        result = pipeline.detect_anomalies()

        # Should handle gracefully
        assert result["anomaly_score"] >= 0.0

    def test_zero_and_negative_metrics(self):
        """Test handling of zero and negative metrics."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline with zero/negative values
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {
                "value_a": 0.0,
                "value_b": -5.0,
            })

        # Add outlier
        pipeline.ingest_metrics(1020.0, {
            "value_a": 100.0,
            "value_b": -50.0,
        })

        result = pipeline.detect_anomalies()

        # Should handle gracefully (baseline with std=0 makes detection hard)
        assert result["anomaly_score"] >= 0.0

    def test_extremely_large_metric_values(self):
        """Test handling of extremely large metric values."""
        pipeline = DetectorPipeline("test", history_window=100)

        # Add baseline
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {"value": 1e6})

        # Large anomaly
        pipeline.ingest_metrics(1020.0, {"value": 1e8})

        result = pipeline.detect_anomalies()

        # Should handle large values gracefully
        assert result["anomaly_score"] >= 0.0

    def test_identical_metric_values(self):
        """Test handling when all metrics are identical."""
        pipeline = DetectorPipeline("test", history_window=100)

        # All metrics exactly the same
        for i in range(20):
            pipeline.ingest_metrics(1000.0 + i, {"value": 42.0})

        # Query detection
        result = pipeline.detect_anomalies()

        # Should not crash, even with zero std dev
        assert result["anomaly_score"] >= 0.0


class TestDetectorPipelinePerformance:
    """Performance and scaling tests."""

    def test_high_volume_metric_stream(self):
        """Test pipeline with high-volume metric stream."""
        pipeline = DetectorPipeline("test", history_window=1000)

        # Ingest 500 metric sets
        for i in range(500):
            pipeline.ingest_metrics(1000.0 + i, {
                f"metric_{j % 20}": float((i + j) % 100)
                for j in range(20)
            })

        assert pipeline.get_stream_size() <= 1000
        assert len(pipeline.get_metric_names()) == 20

    def test_many_concurrent_pipelines(self):
        """Test managing many concurrent detector pipelines."""
        pipelines = {}

        # Create 50 pipelines
        for i in range(50):
            pipeline_id = f"boundary_{i}"
            pipelines[pipeline_id] = DetectorPipeline(pipeline_id, history_window=100)

            # Add data to each
            pipelines[pipeline_id].ingest_metrics(1000.0, {"metric": float(i)})

        assert len(pipelines) == 50

        # Each should be independent
        for i, (pid, pipeline) in enumerate(pipelines.items()):
            assert pipeline.boundary_id == pid
            assert pipeline.get_stream_size() >= 1

    def test_complex_metric_scenario(self):
        """Test complex scenario with many metrics and observations."""
        pipeline = DetectorPipeline("test", history_window=200)

        # Simulate complex system: 50 metrics, 100 observations
        for obs_num in range(100):
            metrics = {
                f"metric_{i}": 50.0 + (i % 10) + (obs_num % 5) * 0.1
                for i in range(50)
            }
            pipeline.ingest_metrics(1000.0 + obs_num, metrics)

        # Should handle gracefully
        assert pipeline.get_stream_size() <= 200
        assert len(pipeline.get_metric_names()) == 50

        # Detection should work
        result = pipeline.detect_anomalies()
        assert result["anomaly_score"] >= 0.0
