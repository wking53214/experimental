"""Tests for Phase 9D (concept drift / adaptive baseline) and 9E (precursors / early warning)."""

import json

import numpy as np
import pytest

from src.governance.phase9d_concept_drift import (
    AdaptiveDetector,
    ConceptDriftDetector,
    OnlineAdaptiveBaseline,
)
from src.governance.phase9e_precursors import AttackPrecursorLearner, EarlyWarningSystem


def _noisy(rng, center, n, sd=1.0):
    return [{"m": float(center + rng.normal(0, sd))} for _ in range(n)]


class TestConceptDriftDetector:
    def test_status_before_data(self):
        status = ConceptDriftDetector().get_drift_status()
        assert status == {"has_drift": False, "drift_score": 0.0, "drift_type": "none",
                          "reanchor_count": 0}

    def test_empty_observation_ignored(self):
        d = ConceptDriftDetector()
        d.update({})
        assert len(d.recent_observations) == 0

    def test_stationary_data_has_no_drift(self):
        rng = np.random.default_rng(0)
        d = ConceptDriftDetector(window_size=50)
        for obs in _noisy(rng, 100, 120):
            d.update(obs)
        assert d.get_drift_status()["has_drift"] is False

    def test_mean_shift_is_detected(self):
        rng = np.random.default_rng(0)
        d = ConceptDriftDetector(window_size=50)
        for obs in _noisy(rng, 100, 20):
            d.update(obs)
        for obs in _noisy(rng, 200, 60):
            d.update(obs)
        status = d.get_drift_status()
        assert status["has_drift"] is True
        assert type(status["drift_score"]) is float
        json.dumps(status)
        assert status["drift_type"] == "sudden"

    def test_kl_identical_is_zero(self):
        d = ConceptDriftDetector()
        m, s = np.array([10.0, 5.0]), np.array([2.0, 1.0])
        assert d._compute_kl_divergence(m, s, m, s) == pytest.approx(0.0, abs=1e-9)

    def test_kl_is_clipped_and_handles_zero_std(self):
        d = ConceptDriftDetector()
        kl = d._compute_kl_divergence(
            np.array([0.0]), np.array([0.0]), np.array([1000.0]), np.array([0.0])
        )
        assert 0.0 <= kl <= 10.0


class TestDriftClassification:
    def test_slow_drift_is_gradual_and_reanchors(self):
        rng = np.random.default_rng(0)
        d = ConceptDriftDetector(window_size=100)
        for i in range(400):  # +4 sigma over 400 steps
            d.update({"m": float(100 + 4.0 * i / 400 + rng.normal(0, 1.0))})
        s = d.get_drift_status()
        assert d.sudden_flagged is False
        assert s["drift_type"] != "sudden"
        assert d.reanchor_count >= 1  # reference follows legitimate drift

    def test_sudden_shift_is_held_until_acknowledged(self):
        rng = np.random.default_rng(0)
        d = ConceptDriftDetector(window_size=50)
        for obs in _noisy(rng, 100, 30):
            d.update(obs)
        for obs in _noisy(rng, 200, 120):  # long after the jump, still flagged
            d.update(obs)
        s = d.get_drift_status()
        assert s["drift_type"] == "sudden" and s["has_drift"] is True
        assert d.reanchor_count == 0
        d.acknowledge()
        for obs in _noisy(rng, 200, 30):
            d.update(obs)
        s = d.get_drift_status()
        assert s["drift_type"] == "none" and s["has_drift"] is False


class TestDriftMultiMetric:
    @staticmethod
    def _stream(seed, steps, shift_after=None, metric=0, sigmas=0.0):
        rng = np.random.default_rng(seed)
        for i in range(steps):
            v = rng.normal(0, 1, 5)
            if shift_after is not None and i >= shift_after:
                v[metric] += sigmas
            yield {f"m{j}": float(x) for j, x in enumerate(v)}

    def test_clean_multimetric_data_never_flagged_sudden(self):
        for seed in range(8):
            d = ConceptDriftDetector()
            for obs in self._stream(seed, 300):
                d.update(obs)
            assert d.get_drift_status()["drift_type"] != "sudden", seed

    def test_single_metric_abrupt_shift_is_flagged(self):
        for seed in range(8):
            d = ConceptDriftDetector()
            for obs in self._stream(seed, 300, shift_after=150, metric=2, sigmas=3.0):
                d.update(obs)
            assert d.get_drift_status()["drift_type"] == "sudden", seed

    def test_status_is_json_serializable(self):
        d = ConceptDriftDetector()
        for obs in self._stream(0, 150):
            d.update(obs)
        json.dumps(d.get_drift_status())


class TestOnlineAdaptiveBaseline:
    def test_uninitialized_baseline(self):
        assert OnlineAdaptiveBaseline().get_current_baseline() == {"status": "not_initialized"}

    def test_accepts_at_threshold_boundary(self):
        b = OnlineAdaptiveBaseline()
        accepted, _ = b.update_with_outlier_rejection({"m": 1.0}, 3.0)
        assert accepted

    def test_rejects_above_threshold_without_changing_baseline(self):
        b = OnlineAdaptiveBaseline()
        b.update_with_outlier_rejection({"m": 100.0}, 0.5)
        before = b.get_current_baseline()["mean"]
        accepted, reason = b.update_with_outlier_rejection({"m": 10_000.0}, 3.5)
        assert not accepted
        assert "Rejected" in reason
        assert b.rejected_count == 1
        assert b.get_current_baseline()["mean"] == before

    def test_baseline_tracks_gradual_drift(self):
        b = OnlineAdaptiveBaseline(decay_factor=0.9)
        b.update_with_outlier_rejection({"m": 100.0}, 0.0)
        for i in range(200):
            b.update_with_outlier_rejection({"m": 100.0 + i * 0.1}, 1.0)
        assert b.get_current_baseline()["mean"][0] > 110.0

    def test_poisoning_attempt_does_not_move_baseline(self):
        b = OnlineAdaptiveBaseline()
        for _ in range(20):
            b.update_with_outlier_rejection({"m": 100.0}, 0.5)
        for _ in range(50):
            b.update_with_outlier_rejection({"m": 1000.0}, 8.0)
        assert b.get_current_baseline()["mean"][0] == pytest.approx(100.0)
        assert b.rejected_count == 50


class TestCalibratedRejection:
    def test_default_cutoff_scales_with_metric_count(self):
        obs = {f"m{i}": 1.0 for i in range(5)}
        assert OnlineAdaptiveBaseline().update_with_outlier_rejection(obs, 4.0)[0] is True
        assert OnlineAdaptiveBaseline().update_with_outlier_rejection(obs, 6.0)[0] is False

    def test_explicit_cutoff_is_respected(self):
        obs = {f"m{i}": 1.0 for i in range(5)}
        assert OnlineAdaptiveBaseline().update_with_outlier_rejection(obs, 4.0, 3.0)[0] is False


class TestAdaptiveDetector:
    def test_summary_counts_and_acceptance_rate(self):
        det = AdaptiveDetector("b")
        for _ in range(8):
            det.process_observation({"m": 100.0}, 0.5)
        for _ in range(2):
            det.process_observation({"m": 500.0}, 9.0)
        s = det.get_adaptation_summary()
        assert s["total_observations"] == 10
        assert s["accepted"] == 8
        assert s["rejected"] == 2
        assert s["acceptance_rate"] == pytest.approx(0.8)

    def test_empty_summary(self):
        assert AdaptiveDetector("b").get_adaptation_summary()["acceptance_rate"] == 0.0


def _feed_pattern(learner, repeats=4):
    """Normal run-up, one elevated step, then a violation."""
    for _ in range(repeats):
        for _ in range(12):
            learner.add_observation({"m": 100.0})
        learner.add_observation({"m": 400.0})
        learner.add_observation({"m": 500.0}, is_violation=True)


class TestAttackPrecursorLearner:
    def test_too_few_violations_learns_nothing(self):
        learner = AttackPrecursorLearner(min_patterns=3)
        learner.add_observation({"m": 1.0}, is_violation=True)
        assert learner.learn_precursors() == {}

    def test_normal_signature_never_learned(self):
        learner = AttackPrecursorLearner(min_patterns=3)
        _feed_pattern(learner, repeats=4)
        patterns = learner.learn_precursors()
        assert "normal" not in patterns and "unknown" not in patterns

    def test_ordinary_observation_not_flagged_after_learning(self):
        ews = EarlyWarningSystem("b")
        _feed_pattern(ews.precursor_learner, repeats=4)
        ews.update_from_violation()
        for _ in range(5):
            ews.process_observation({"m": 100.0}, anomaly_score=0.0)
        r = ews.process_observation({"m": 100.0}, anomaly_score=0.0)
        assert r["warning_level"] == "normal"

    def test_detect_without_patterns_returns_none(self):
        assert AttackPrecursorLearner().detect_precursor({"m": 1.0}) is None

    def test_violation_indices_recorded(self):
        learner = AttackPrecursorLearner()
        for _ in range(5):
            learner.add_observation({"m": 1.0})
        learner.add_observation({"m": 9.0}, is_violation=True)
        assert learner.violation_indices == [5]

    def test_learns_signature_and_lead_time(self):
        learner = AttackPrecursorLearner(min_patterns=3)
        _feed_pattern(learner, repeats=4)
        patterns = learner.learn_precursors()
        assert patterns
        assert all(0.0 < p["confidence"] <= 1.0 for p in patterns.values())
        assert all(1.0 <= p["lead_time_steps"] <= 5.0 for p in patterns.values())

    def test_detects_learned_precursor(self):
        learner = AttackPrecursorLearner(min_patterns=3)
        _feed_pattern(learner, repeats=4)
        learner.learn_precursors()
        hit = learner.detect_precursor({"m": 400.0})
        assert hit is not None and hit["precursor_detected"] is True
        assert learner.pattern_detections

    def test_normal_observation_not_flagged(self):
        learner = AttackPrecursorLearner(min_patterns=3)
        _feed_pattern(learner, repeats=4)
        learner.learn_precursors()
        learner.learned_patterns.pop("normal", None)
        assert learner.detect_precursor({"m": 100.0}) is None

    def test_summary_shape(self):
        learner = AttackPrecursorLearner()
        s = learner.get_precursor_summary()
        assert s["patterns_learned"] == 0
        assert s["predictive_accuracy"] == 0.0

    def test_violation_index_stays_valid_after_eviction(self):
        learner = AttackPrecursorLearner(lookback_window=2)  # maxlen 20
        for _ in range(30):
            learner.add_observation({"m": 1.0})
        learner.add_observation({"m": 99.0}, is_violation=True)
        learner.add_observation({"m": 1.0})  # evicts oldest
        rel = learner.violation_indices[0] - learner._history_offset()
        assert list(learner.observation_history)[rel]["m"] == 99.0

    def test_evicted_violations_are_dropped(self):
        learner = AttackPrecursorLearner(lookback_window=2)  # maxlen 20
        learner.add_observation({"m": 99.0}, is_violation=True)
        for _ in range(25):
            learner.add_observation({"m": 1.0})
        assert learner.violation_indices == []

    def test_learning_works_after_history_wraps(self):
        learner = AttackPrecursorLearner(lookback_window=10, min_patterns=3)  # maxlen 100
        for _ in range(150):
            learner.add_observation({"m": 100.0})
        _feed_pattern(learner, repeats=4)
        assert learner.learn_precursors()


class TestEarlyWarningSystem:
    def _primed(self):
        ews = EarlyWarningSystem("b")
        for _ in range(30):
            ews.process_observation({"m": 100.0}, anomaly_score=0.0)
        ews.precursor_learner.learned_patterns["elevated:0"] = {
            "signature": "elevated:0",
            "lead_time_steps": 2.0,
            "occurrences": 4,
            "confidence": 0.8,
        }
        return ews

    def test_normal_when_no_precursor(self):
        r = EarlyWarningSystem("b").process_observation({"m": 1.0}, 0.9)
        assert r["warning_level"] == "normal"
        assert r["confidence"] == 0.0

    def test_precursor_alone_is_elevated_at_half_confidence(self):
        r = self._primed().process_observation({"m": 400.0}, anomaly_score=0.1)
        assert r["warning_level"] == "elevated"
        assert r["confidence"] == pytest.approx(0.4)

    def test_precursor_plus_anomaly_is_critical(self):
        r = self._primed().process_observation({"m": 400.0}, anomaly_score=0.7)
        assert r["warning_level"] == "critical"
        assert r["confidence"] == pytest.approx(0.9)

    def test_summary_counts(self):
        ews = self._primed()
        ews.process_observation({"m": 400.0}, 0.1)
        ews.process_observation({"m": 400.0}, 0.7)
        ews.process_observation({"m": 500.0}, 0.9, is_violation=True)
        s = ews.get_early_warning_summary()
        assert s["total_observations"] == 33
        assert s["elevated_warnings"] >= 1
        assert s["critical_warnings"] >= 1
        assert s["violations"] == 1

    def test_update_from_violation_relearns(self):
        ews = EarlyWarningSystem("b")
        _feed_pattern(ews.precursor_learner, repeats=4)
        ews.update_from_violation()
        assert ews.precursor_learner.learned_patterns
