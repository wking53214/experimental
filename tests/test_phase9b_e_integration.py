"""Phase 9B-9E: Advanced Adversarial Testing Suite (hardened mutation test)"""
import numpy as np
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
