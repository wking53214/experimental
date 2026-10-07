"""
Phase 9A: Multivariate Gaussian Anomaly Detection Tests

Tests the generative anomaly detection model that learns the full
covariance structure of normal system behavior and catches novel attacks
via Mahalanobis distance scoring.
"""

import numpy as np
import pytest
from src.governance.multivariate import (
    MultivariateGaussian, MahalanobisAnomalyScorer, InvariantLearner, GenerativeAnomalyDetector
)


class TestMultivariateGaussian:
    """Test multivariate Gaussian model learning."""

    def test_initialize_model(self):
        """Test model initialization."""
        metrics = ["error_rate", "throughput", "latency"]
        model = MultivariateGaussian(metrics)

        assert model.n_metrics == 3
        assert model.n == 0
        assert len(model.mean) == 3

    def test_single_observation_update(self):
        """Test updating with single observation."""
        model = MultivariateGaussian(["metric_a", "metric_b"])

        obs = {"metric_a": 1.0, "metric_b": 2.0}
        model.update(obs)

        assert model.n == 1
        np.testing.assert_array_almost_equal(model.mean, [1.0, 2.0])

    def test_mean_calculation(self):
        """Test that mean converges correctly."""
        model = MultivariateGaussian(["value"])

        # Add observations with known mean
        for i in range(10):
            model.update({"value": float(i)})

        # Mean should be ~4.5
        assert 4.0 < model.mean[0] < 5.0

    def test_covariance_calculation(self):
        """Test covariance matrix calculation."""
        model = MultivariateGaussian(["x", "y"])

        # Create correlated data: y = 2*x + noise
        np.random.seed(42)
        for i in range(50):
            x_val = float(i) / 10
            y_val = 2.0 * x_val + np.random.normal(0, 0.1)
            model.update({"x": x_val, "y": y_val})

        cov = model.get_covariance()

        # Should be positive definite
        eigenvalues = np.linalg.eigvalsh(cov)
        assert np.all(eigenvalues > 0)

        # Off-diagonal should indicate positive correlation
        assert cov[0, 1] > 0

    def test_mahalanobis_distance_normal(self):
        """Test Mahalanobis distance for normal observations."""
        model = MultivariateGaussian(["metric"])

        # Train on constant value
        for i in range(20):
            model.update({"metric": 100.0})

        # Distance to mean should be near zero
        md = model.mahalanobis_distance({"metric": 100.0})
        assert md < 0.1

    def test_mahalanobis_distance_anomalous(self):
        """Test Mahalanobis distance for anomalous observations."""
        model = MultivariateGaussian(["metric"])

        # Train on values around 100
        for i in range(20):
            model.update({"metric": 100.0 + np.random.normal(0, 1.0)})

        # Distance to far-off value should be large
        md = model.mahalanobis_distance({"metric": 150.0})
        assert md > 10.0

    def test_mahalanobis_multivariate(self):
        """Test Mahalanobis distance with multiple metrics."""
        model = MultivariateGaussian(["x", "y"])

        # Train on data with correlation
        np.random.seed(42)
        for i in range(50):
            x = np.random.normal(10.0, 1.0)
            y = x + np.random.normal(0, 0.5)  # Correlated
            model.update({"x": x, "y": y})

        # Normal observation (on the correlation line)
        md_normal = model.mahalanobis_distance({"x": 10.0, "y": 10.0})

        # Anomalous observation (off correlation line)
        md_anomalous = model.mahalanobis_distance({"x": 10.0, "y": 20.0})

        assert md_anomalous > md_normal * 2

    def test_log_likelihood(self):
        """Test log-likelihood scoring."""
        model = MultivariateGaussian(["metric"])

        for i in range(20):
            model.update({"metric": 100.0 + np.random.normal(0, 1.0)})

        ll_normal = model.log_likelihood({"metric": 100.0})
        ll_anomalous = model.log_likelihood({"metric": 150.0})

        # Normal observation should have higher likelihood
        assert ll_normal > ll_anomalous


class TestMahalanobisScorer:
    """Test anomaly scoring based on Mahalanobis distance."""

    def test_normal_distance_is_zero_score(self):
        """Test that normal distances (< threshold) score 0."""
        scorer = MahalanobisAnomalyScorer(threshold=3.0)

        assert scorer.score(0.0) == 0.0
        assert scorer.score(1.0) == 0.0
        assert scorer.score(2.9) == 0.0

    def test_anomalous_distance_scores_high(self):
        """Test that anomalous distances score high."""
        scorer = MahalanobisAnomalyScorer(threshold=3.0, saturation=5.0)

        assert scorer.score(5.0) == 1.0  # At saturation
        assert scorer.score(10.0) == 1.0  # Beyond saturation

    def test_score_interpolation(self):
        """Test interpolation between threshold and saturation."""
        scorer = MahalanobisAnomalyScorer(threshold=3.0, saturation=5.0)

        # Midpoint should be ~0.5
        score_mid = scorer.score(4.0)
        assert 0.4 < score_mid < 0.6

    def test_score_bounds(self):
        """Test that scores stay in [0, 1]."""
        scorer = MahalanobisAnomalyScorer()

        for dist in [0, 1, 3, 5, 10, 100]:
            score = scorer.score(float(dist))
            assert 0.0 <= score <= 1.0


class TestInvariantLearner:
    """Test metric invariant learning."""

    def test_initialize_learner(self):
        """Test learner initialization."""
        learner = InvariantLearner(min_observations=10)

        assert learner.min_observations == 10
        assert len(learner.observations) == 0

    def test_add_observations(self):
        """Test adding observations."""
        learner = InvariantLearner(min_observations=5)

        for i in range(3):
            learner.add_observation({"metric": float(i)})

        assert len(learner.observations) == 3

    def test_learn_positive_correlation(self):
        """Test learning positive correlation between metrics."""
        learner = InvariantLearner(min_observations=10)

        # Create positively correlated data
        np.random.seed(42)
        for i in range(30):
            x = float(i)
            y = 2.0 * x + np.random.normal(0, 2.0)
            learner.add_observation({"error_rate": x, "latency": y})

        invariants = learner.learn_invariants()

        # Should find positive correlation
        assert ("error_rate", "latency") in invariants
        assert invariants[("error_rate", "latency")]["correlation"] > 0.8

    def test_learn_negative_correlation(self):
        """Test learning negative correlation between metrics."""
        learner = InvariantLearner(min_observations=10)

        # Create negatively correlated data
        np.random.seed(42)
        for i in range(30):
            x = float(i)
            y = -1.5 * x + np.random.normal(0, 2.0)
            learner.add_observation({"requests": x, "response_time": y})

        invariants = learner.learn_invariants()

        # Should find negative correlation
        assert ("requests", "response_time") in invariants
        assert invariants[("requests", "response_time")]["correlation"] < -0.7


class TestGenerativeAnomalyDetector:
    """Test end-to-end generative anomaly detection."""

    def test_initialize_detector(self):
        """Test detector initialization."""
        detector = GenerativeAnomalyDetector("test_boundary")

        assert detector.boundary_id == "test_boundary"
        assert detector.model is None
        assert not detector.model_locked

    def test_model_learning_phase(self):
        """Test detector during model learning phase."""
        detector = GenerativeAnomalyDetector("test", min_observations=10)

        # Add some observations
        for i in range(5):
            detector.ingest_observation({"metric": 100.0})

        result = detector.detect_anomaly({"metric": 100.0})

        # Should not detect anomalies during learning
        assert not result["anomaly_detected"]
        assert "learning" in result["reason"].lower()

    def test_model_locking(self):
        """Test that model locks after sufficient observations."""
        detector = GenerativeAnomalyDetector("test", min_observations=10)

        # Add exactly 10 observations
        for i in range(10):
            detector.ingest_observation({"metric": 100.0})

        assert detector.model_locked

    def test_normal_operation_no_anomaly(self):
        """Test that normal observations are not flagged."""
        detector = GenerativeAnomalyDetector("test", min_observations=15)

        # Train on normal data
        np.random.seed(42)
        for i in range(20):
            detector.ingest_observation({
                "error_rate": 0.05 + np.random.normal(0, 0.01),
                "throughput": 1000.0 + np.random.normal(0, 50.0),
            })

        # Test normal observation
        result = detector.detect_anomaly({
            "error_rate": 0.05,
            "throughput": 1000.0,
        })

        assert not result["anomaly_detected"]
        assert result["anomaly_score"] < 0.1

    def test_anomalous_operation_detected(self):
        """Test that anomalous observations are flagged."""
        detector = GenerativeAnomalyDetector("test", min_observations=15, mahalanobis_threshold=2.0)

        # Train on normal data
        for i in range(20):
            detector.ingest_observation({
                "metric_a": 100.0 + np.random.normal(0, 1.0),
                "metric_b": 50.0 + np.random.normal(0, 1.0),
            })

        # Test far-off observation (5σ away)
        result = detector.detect_anomaly({
            "metric_a": 150.0,  # 5σ from mean
            "metric_b": 50.0,
        })

        assert result["anomaly_detected"]
        assert result["anomaly_score"] > 0.5

    def test_novel_attack_detection(self):
        """Test detection of novel attack (metric combination never seen)."""
        detector = GenerativeAnomalyDetector("test", min_observations=15)

        # Train: normal operation has error_rate ≈ throughput
        np.random.seed(42)
        for i in range(25):
            base = 50.0 + np.random.normal(0, 2.0)
            detector.ingest_observation({
                "error_count": base,
                "request_count": base * 20 + np.random.normal(0, 10.0),
            })

        # Novel attack: decouple the metrics (gaming)
        # Drop error_count but maintain request_count (never seen before)
        result = detector.detect_anomaly({
            "error_count": 10.0,  # Anomalously low
            "request_count": 1000.0,  # Normal
        })

        # Multivariate model should catch this distortion
        assert result["mahalanobis_distance"] > 2.0

    def test_slow_burn_degradation(self):
        """Test detection of gradual metric degradation."""
        detector = GenerativeAnomalyDetector("test", min_observations=15)

        # Build baseline with stable metrics
        for i in range(20):
            detector.ingest_observation({
                "latency_p99": 100.0 + np.random.normal(0, 2.0),
                "success_rate": 0.99 + np.random.normal(0, 0.01),
            })

        # Gradual degradation: latency slowly increases while success drops
        # This is a novel pattern (negative correlation inversion)
        result = detector.detect_anomaly({
            "latency_p99": 120.0,  # 10% worse
            "success_rate": 0.97,  # Also degraded
        })

        # Should flag multivariate anomaly
        assert result["mahalanobis_distance"] > 1.5

    def test_pareto_gaming_detection(self):
        """Test detection of Pareto gaming (optimize one metric at cost of others)."""
        detector = GenerativeAnomalyDetector("test", min_observations=20)

        # Normal: error_rate and latency correlated, throughput inversely correlated
        np.random.seed(42)
        for i in range(25):
            errors = 0.05 + np.random.normal(0, 0.005)
            detector.ingest_observation({
                "error_rate": errors,
                "latency": 50.0 + errors * 1000.0,  # Positive correlation
                "throughput": 1000.0 - errors * 5000.0,  # Negative correlation
            })

        # Attack: suppress errors artificially, collapse throughput (gaming)
        result = detector.detect_anomaly({
            "error_rate": 0.01,  # Artificially low
            "latency": 45.0,  # Normal
            "throughput": 200.0,  # Collapsed
        })

        # Multivariate model should catch distorted covariance
        assert result["mahalanobis_distance"] > 2.5


class TestGenerativeDetectorScale:
    """Test generative detection at scale."""

    def test_many_metrics(self):
        """Test detector with many metrics."""
        metric_names = [f"metric_{i}" for i in range(20)]
        detector = GenerativeAnomalyDetector("test", min_observations=30)

        # Add observations
        np.random.seed(42)
        for obs_i in range(40):
            obs = {name: float(obs_i) + np.random.normal(0, 1.0) for name in metric_names}
            detector.ingest_observation(obs)

        # Test normal observation
        test_obs = {name: 20.0 + np.random.normal(0, 1.0) for name in metric_names}
        result = detector.detect_anomaly(test_obs)

        assert result["anomaly_score"] >= 0.0
        assert len(result) > 0

    def test_singular_covariance_handling(self):
        """Test graceful handling of singular covariance."""
        detector = GenerativeAnomalyDetector("test", min_observations=5, regularization=1e-6)

        # Create perfectly correlated data (will be singular without regularization)
        for i in range(20):
            detector.ingest_observation({
                "metric_a": float(i),
                "metric_b": float(i) * 2,  # Perfect correlation
            })

        result = detector.detect_anomaly({
            "metric_a": 10.0,
            "metric_b": 20.0,
        })

        # Should handle gracefully
        assert "anomaly_score" in result
        assert not np.isnan(result["anomaly_score"])


class TestGenerativeDetectorPerformance:
    """Performance and stress tests."""

    def test_high_frequency_ingestion(self):
        """Test handling high-frequency metric ingestion."""
        detector = GenerativeAnomalyDetector("test", min_observations=20)

        # Ingest 500 observations
        for i in range(500):
            detector.ingest_observation({
                "value": float(i % 100) + np.random.normal(0, 1.0),
            })

        result = detector.detect_anomaly({"value": 50.0})
        assert result is not None

    def test_detector_efficiency(self):
        """Test that detector doesn't get slower with more observations."""
        detector = GenerativeAnomalyDetector("test", min_observations=20)

        import time

        # Build model
        for i in range(100):
            detector.ingest_observation({
                "metric": float(i % 50),
            })

        # Time detection calls
        times = []
        for i in range(100):
            start = time.time()
            detector.detect_anomaly({"metric": 25.0})
            times.append(time.time() - start)

        avg_time = np.mean(times)
        # Detection should be very fast (< 1ms even at scale)
        assert avg_time < 0.001


class TestCalibratedThreshold:
    """Default Mahalanobis threshold scales with the number of metrics."""

    @pytest.mark.parametrize("k,exact", [(1, 3.291), (5, 4.529), (10, 5.439), (20, 6.73)])
    def test_matches_chi_square_quantile(self, k, exact):
        from src.governance.multivariate import calibrated_mahalanobis_threshold
        assert calibrated_mahalanobis_threshold(k) == pytest.approx(exact, rel=0.02)

    def test_default_detector_calibrates_on_first_observation(self):
        d = GenerativeAnomalyDetector("t")
        assert d.scorer.threshold == 3.0  # placeholder until the model exists
        d.ingest_observation({f"m{i}": 1.0 for i in range(5)})
        assert d.scorer.threshold == pytest.approx(4.53, rel=0.02)
        assert d.scorer.saturation == pytest.approx(d.scorer.threshold + 2.0)

    def test_explicit_threshold_is_respected(self):
        d = GenerativeAnomalyDetector("t", mahalanobis_threshold=2.0)
        d.ingest_observation({"a": 1.0, "b": 2.0})
        assert d.scorer.threshold == 2.0

    def test_low_false_alarm_rate_on_clean_multivariate_data(self):
        rng = np.random.default_rng(0)
        names = [f"m{i}" for i in range(5)]
        d = GenerativeAnomalyDetector("t", min_observations=30)
        for _ in range(150):
            d.ingest_observation(dict(zip(names, rng.normal(0, 1, 5))))
        flags = 0
        for _ in range(500):
            obs = dict(zip(names, rng.normal(0, 1, 5)))
            flags += d.detect_anomaly(obs)["anomaly_detected"]
        assert flags / 500 < 0.03
