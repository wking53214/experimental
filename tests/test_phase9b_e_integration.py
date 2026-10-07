"""Phase 9B-9E: Advanced Adversarial Testing Suite (hardened mutation test)"""
import numpy as np
import pytest
from src.governance.phase9_integration import HybridDetectorPipeline
from src.governance.phase9c_evolutionary import EvolutionaryAdversary
from src.governance.phase9d_concept_drift import ConceptDriftDetector, OnlineAdaptiveBaseline, AdaptiveDetector
from src.governance.phase9e_precursors import AttackPrecursorLearner, EarlyWarningSystem


class TestPhase9B:
    def test_hybrid_detector_initialization(self):
        detector = HybridDetectorPipeline("test", history_window=100)
        assert detector.boundary_id == "test"
        assert detector.generative_detector is not None

    def test_hybrid_detector_ingestion(self):
        detector = HybridDetectorPipeline("test")
        for i in range(20):
            detector.ingest_metrics(1000.0 + i, {"metric_a": 100.0, "metric_b": 50.0})
        assert detector.observation_count >= 20
        assert detector.generative_detector.observation_count >= 20

    def test_hybrid_detection_result(self):
        detector = HybridDetectorPipeline("test", history_window=100)
        for i in range(25):
            detector.ingest_metrics(1000.0 + i, {"metric": 100.0})
        result = detector.detect_anomalies()
        assert "anomaly_detected" in result
        assert "traditional_score" in result
        assert "generative_score" in result
        assert "detection_method" in result


class TestPhase9C:
    def test_evolutionary_adversary_init(self):
        adversary = EvolutionaryAdversary(population_size=10, generations=3)
        assert adversary.population_size == 10
        assert adversary.generations == 3

    def test_population_generation(self):
        adversary = EvolutionaryAdversary()
        metrics = ["a", "b"]
        population = adversary.generate_initial_population(metrics)
        assert len(population) == 5 or len(population) >= 1
        for chromosome in population:
            assert len(chromosome.metric_targets) > 0
            assert len(chromosome.magnitudes) == len(chromosome.metric_targets)

    def test_mutation(self):
        """Test chromosome mutation (retry under stochastic no-ops)."""
        adversary = EvolutionaryAdversary()
        metrics = ["a", "b"]
        original = adversary.generate_initial_population(metrics)[0]
        changed = False
        for _ in range(10):
            mutant = adversary.mutate(original, metrics)
            if (mutant.magnitudes != original.magnitudes or
                mutant.durations != original.durations or
                mutant.delays != original.delays):
                changed = True
                break
        assert changed, "Mutation should alter at least one chromosome field within 10 tries"

    def test_crossover(self):
        adversary = EvolutionaryAdversary()
        metrics = ["a", "b"]
        pop = adversary.generate_initial_population(metrics)
        if len(pop) >= 2:
            child = adversary.crossover(pop[0], pop[1], metrics)
            assert len(child.metric_targets) > 0
            assert len(child.magnitudes) == len(child.metric_targets)

    def test_fitness_evaluation(self):
        adversary = EvolutionaryAdversary()
        metrics = ["a", "b"]
        chromosome = adversary.generate_initial_population(metrics)[0]
        fitness1 = adversary.evaluate_fitness(chromosome, damage=0.8, detected=False)
        fitness2 = adversary.evaluate_fitness(chromosome, damage=0.8, detected=True)
        assert fitness1 > fitness2


class TestHybridGenerativeWiring:
    """The generative layer must score the real latest observation."""

    @staticmethod
    def _sample(rng):
        return {"a": float(rng.normal(100, 5)), "b": float(rng.normal(50, 3))}

    def test_clean_data_not_flagged_by_generative_layer(self):
        rng = np.random.default_rng(1)
        det = HybridDetectorPipeline("w", history_window=100)
        flags = 0
        for i in range(200):
            det.ingest_metrics(1000.0 + i, self._sample(rng))
            if i >= 40:
                flags += det.detect_anomalies()["generative_detection"]["anomaly_detected"]
        assert flags / 160 < 0.25

    def test_generative_layer_sees_anomalous_observation(self):
        rng = np.random.default_rng(2)
        det = HybridDetectorPipeline("w", history_window=100)
        for i in range(60):
            det.ingest_metrics(1000.0 + i, self._sample(rng))
        det.ingest_metrics(2000.0, {"a": 100.0 + 8 * 5, "b": 50.0 - 8 * 3})
        r = det.detect_anomalies()
        assert r["generative_score"] > 0.5
        assert r["mahalanobis_distance"] > 3.0

    def test_empty_stream_does_not_crash(self):
        r = HybridDetectorPipeline("w").detect_anomalies()
        assert r["anomaly_detected"] is False


class TestHybridCutoff:
    """Each layer keeps its own decision rule; the hybrid alarms if either fires."""

    @staticmethod
    def _clean_hybrid():
        rng = np.random.default_rng(3)
        det = HybridDetectorPipeline("c", history_window=100)
        for i in range(80):
            det.ingest_metrics(1000.0 + i, {"a": float(rng.normal(100, 5)), "b": float(rng.normal(50, 3))})
        return det

    def test_mid_score_below_traditional_threshold_not_flagged(self, monkeypatch):
        from src.governance.metrics import DetectorPipeline
        det = self._clean_hybrid()
        monkeypatch.setattr(DetectorPipeline, "detect_anomalies",
                            lambda self: {"anomaly_detected": False, "anomaly_score": 0.7})
        r = det.detect_anomalies()
        assert r["anomaly_detected"] is False
        assert r["anomaly_score"] == pytest.approx(0.7)

    def test_traditional_detection_is_flagged(self, monkeypatch):
        from src.governance.metrics import DetectorPipeline
        det = self._clean_hybrid()
        monkeypatch.setattr(DetectorPipeline, "detect_anomalies",
                            lambda self: {"anomaly_detected": True, "anomaly_score": 0.9})
        assert det.detect_anomalies()["anomaly_detected"] is True
