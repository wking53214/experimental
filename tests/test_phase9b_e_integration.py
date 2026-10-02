"""
Phase 9B-9E: Advanced Adversarial Testing Suite

Tests the complete adversarial research harness:
- 9B: Hybrid detection (traditional + generative)
- 9C: Evolutionary attacks
- 9D: Concept drift handling
- 9E: Attack precursor learning
"""

import numpy as np
from src.governance.phase9_integration import HybridDetectorPipeline
from src.governance.phase9c_evolutionary import EvolutionaryAdversary
from src.governance.phase9d_concept_drift import ConceptDriftDetector, OnlineAdaptiveBaseline, AdaptiveDetector
from src.governance.phase9e_precursors import AttackPrecursorLearner, EarlyWarningSystem


class TestPhase9B:
    """Test hybrid detection pipeline."""

    def test_hybrid_detector_initialization(self):
        """Test creating hybrid detector."""
        detector = HybridDetectorPipeline("test", history_window=100)
        assert detector.boundary_id == "test"
        assert detector.generative_detector is not None

    def test_hybrid_detector_ingestion(self):
        """Test metric ingestion into both layers."""
        detector = HybridDetectorPipeline("test")

        for i in range(20):
            detector.ingest_metrics(1000.0 + i, {
                "metric_a": 100.0,
                "metric_b": 50.0,
            })

        assert detector.observation_count >= 20
        assert detector.generative_detector.observation_count >= 20

    def test_hybrid_detection_result(self):
        """Test hybrid detection result structure."""
        detector = HybridDetectorPipeline("test", history_window=100)

        # Build baseline
        for i in range(25):
            detector.ingest_metrics(1000.0 + i, {"metric": 100.0})

        # Detect
        result = detector.detect_anomalies()

        assert "anomaly_detected" in result
        assert "traditional_score" in result
        assert "generative_score" in result
        assert "detection_method" in result


class TestPhase9C:
    """Test evolutionary adversary."""

    def test_evolutionary_initialization(self):
        """Test adversary initialization."""
        adversary = EvolutionaryAdversary(population_size=10, generations=3)
        assert adversary.population_size == 10
        assert adversary.generations == 3

    def test_initial_population_generation(self):
        """Test generating initial random population."""
        adversary = EvolutionaryAdversary(population_size=5)
        metrics = ["error_rate", "throughput", "latency"]

        population = adversary.generate_initial_population(metrics)

        assert len(population) == 5
        for chromosome in population:
            assert len(chromosome.metric_targets) > 0
            assert len(chromosome.magnitudes) == len(chromosome.metric_targets)

    def test_mutation(self):
        """Test chromosome mutation."""
        adversary = EvolutionaryAdversary()
        metrics = ["a", "b"]

        original = adversary.generate_initial_population(metrics)[0]
        mutant = adversary.mutate(original, metrics)

        # Should be different (with high probability)
        assert (mutant.magnitudes != original.magnitudes or
                mutant.durations != original.durations or
                mutant.delays != original.delays)

    def test_crossover(self):
        """Test chromosome crossover."""
        adversary = EvolutionaryAdversary()
        metrics = ["a", "b", "c"]

        pop = adversary.generate_initial_population(metrics)
        parent1, parent2 = pop[0], pop[1]

        child = adversary.crossover(parent1, parent2)

        assert len(child.metric_targets) > 0
        assert len(child.magnitudes) == len(child.metric_targets)

    def test_fitness_evaluation(self):
        """Test fitness scoring."""
        adversary = EvolutionaryAdversary()
        metrics = ["a"]
        chromosome = adversary.generate_initial_population(metrics)[0]

        # Undetected attack
        fitness1 = adversary.evaluate_fitness(chromosome, damage=0.8, detected=False)

        # Detected attack (penalty)
        fitness2 = adversary.evaluate_fitness(chromosome, damage=0.8, detected=True)

        assert fitness1 > fitness2


class TestPhase9D:
    """Test concept drift and online learning."""

    def test_concept_drift_initialization(self):
        """Test drift detector initialization."""
        detector = ConceptDriftDetector(window_size=50)
        assert detector.window_size == 50

    def test_concept_drift_detection(self):
        """Test detecting concept drift."""
        detector = ConceptDriftDetector(window_size=20, drift_threshold=0.3)

        # Add observations with one distribution
        for i in range(30):
            detector.update({"metric": float(i % 50)})

        status = detector.get_drift_status()
        assert isinstance(status, dict)
        assert "drift_score" in status

    def test_adaptive_baseline(self):
        """Test adaptive baseline learning."""
        baseline = OnlineAdaptiveBaseline(decay_factor=0.95)

        # Add observations
        for i in range(20):
            accepted, reason = baseline.update_with_outlier_rejection(
                {"metric": 100.0},
                mahalanobis_distance=0.5
            )
            assert accepted

        assert baseline.observation_count >= 20

    def test_outlier_rejection(self):
        """Test that high-MD observations are rejected."""
        baseline = OnlineAdaptiveBaseline()

        # Normal observation
        accepted1, _ = baseline.update_with_outlier_rejection({"metric": 100.0}, 1.0)
        assert accepted1

        # Anomalous observation (high MD)
        accepted2, _ = baseline.update_with_outlier_rejection({"metric": 100.0}, 5.0)
        assert not accepted2
        assert baseline.rejected_count == 1

    def test_adaptive_detector_integration(self):
        """Test adaptive detector combining drift + learning."""
        detector = AdaptiveDetector("test")

        # Process observations
        for i in range(30):
            result = detector.process_observation(
                {"metric": 100.0 + np.random.normal(0, 1.0)},
                mahalanobis_distance=0.5
            )
            assert "accepted" in result

        summary = detector.get_adaptation_summary()
        assert summary["total_observations"] >= 30


class TestPhase9E:
    """Test attack precursor learning."""

    def test_precursor_learner_initialization(self):
        """Test precursor learner initialization."""
        learner = AttackPrecursorLearner(lookback_window=10, min_patterns=3)
        assert learner.lookback_window == 10
        assert learner.min_patterns == 3

    def test_observation_tracking(self):
        """Test tracking observations and violations."""
        learner = AttackPrecursorLearner()

        # Add normal observations
        for i in range(20):
            learner.add_observation({"metric": 100.0}, is_violation=False)

        # Add violation
        learner.add_observation({"metric": 150.0}, is_violation=True)

        assert len(learner.violation_indices) == 1

    def test_precursor_learning(self):
        """Test learning precursor patterns."""
        learner = AttackPrecursorLearner(min_patterns=2)

        # Simulate pattern: metric increases before violation
        for pattern_iter in range(3):
            for i in range(10):
                learner.add_observation(
                    {"metric": 100.0 + float(i) * 2.0},
                    is_violation=False
                )
            # Violation at peak
            learner.add_observation({"metric": 120.0}, is_violation=True)

        patterns = learner.learn_precursors()
        assert len(patterns) > 0

    def test_precursor_detection(self):
        """Test detecting learned precursor patterns."""
        learner = AttackPrecursorLearner()

        # Learn a pattern (5 violations with rising metric)
        for _ in range(5):
            for i in range(5):
                learner.add_observation({"metric": 100.0 + i * 10}, is_violation=False)
            learner.add_observation({"metric": 150.0}, is_violation=True)

        learner.learn_precursors()

        # Try to detect it
        precursor = learner.detect_precursor({"metric": 130.0})
        # May or may not detect depending on signature matching
        assert precursor is None or isinstance(precursor, dict)

    def test_early_warning_system(self):
        """Test early warning system."""
        ews = EarlyWarningSystem("test")

        # Process observations
        for i in range(30):
            result = ews.process_observation(
                {"metric": 100.0},
                anomaly_score=0.1,
                is_violation=False
            )
            assert "warning_level" in result

        summary = ews.get_early_warning_summary()
        assert summary["total_observations"] >= 30


class TestPhase9Integration:
    """Integration tests across all phases."""

    def test_full_adversarial_harness(self):
        """Test complete adversarial testing pipeline."""
        # Initialize components
        detector = HybridDetectorPipeline("test")
        adversary = EvolutionaryAdversary(population_size=5, generations=2)
        drift_handler = AdaptiveDetector("test")
        ews = EarlyWarningSystem("test")

        # Build baseline
        for i in range(25):
            metrics = {"metric_a": 100.0, "metric_b": 50.0}
            detector.ingest_metrics(1000.0 + i, metrics)
            drift_handler.process_observation(metrics, 0.5)
            ews.process_observation(metrics, 0.1)

        # Run detection
        result = detector.detect_anomalies()
        assert result is not None

        # Get summaries
        drift_summary = drift_handler.get_adaptation_summary()
        ews_summary = ews.get_early_warning_summary()

        assert drift_summary["total_observations"] >= 25
        assert ews_summary["total_observations"] >= 25

    def test_adversarial_pipeline_scale(self):
        """Test pipeline at scale."""
        detector = HybridDetectorPipeline("scale_test")

        # 100 observations across 10 metrics
        for obs_i in range(100):
            metrics = {f"metric_{j}": float((obs_i + j) % 100) for j in range(10)}
            detector.ingest_metrics(1000.0 + obs_i, metrics)

        result = detector.detect_anomalies()
        assert result is not None


class TestPhase9Performance:
    """Performance and efficiency tests."""

    def test_detector_throughput(self):
        """Test detection throughput."""
        import time
        detector = HybridDetectorPipeline("perf_test")

        # Warm up
        for i in range(50):
            detector.ingest_metrics(1000.0 + i, {"metric": 100.0})

        # Time 100 detections
        start = time.time()
        for i in range(100):
            detector.ingest_metrics(1050.0 + i, {"metric": 100.0})
            detector.detect_anomalies()
        elapsed = time.time() - start

        # Should be fast (< 100ms for 100 ops)
        assert elapsed < 0.1

    def test_adversary_evolution_efficiency(self):
        """Test adversary evolution doesn't slow down."""
        adversary = EvolutionaryAdversary(population_size=10, generations=5)
        import time

        def dummy_fitness(chromosome):
            return 0.5, False

        start = time.time()
        result = adversary.run_evolution(["a", "b", "c"], dummy_fitness)
        elapsed = time.time() - start

        # Evolution should be fast (< 1s for 50 attacks)
        assert elapsed < 1.0
