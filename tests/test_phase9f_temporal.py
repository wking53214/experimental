"""Tests for Phase 9F temporal shift detection."""
import numpy as np
import pytest

from src.governance.phase9_integration import HybridDetectorPipeline
from src.governance.phase9f_temporal import TemporalShiftDetector

SD = np.array([1.0, 1.0, 1.0])


def _obs(rng, shift=None):
    v = rng.normal(0, 1, 3)
    if shift is not None:
        v = v + shift
    return {"a": float(v[0]), "b": float(v[1]), "c": float(v[2])}


def _trained(seed, **kw):
    rng = np.random.default_rng(seed)
    d = TemporalShiftDetector(**kw)
    for _ in range(100):
        d.update(_obs(rng))
    return d, rng


def test_invalid_smoothing_rejected():
    with pytest.raises(ValueError):
        TemporalShiftDetector(smoothing=0.0)


def test_never_alarms_while_learning():
    d = TemporalShiftDetector(min_observations=50)
    rng = np.random.default_rng(0)
    for _ in range(49):
        r = d.update(_obs(rng, shift=100.0))
        assert r["anomaly_detected"] is False
    assert d.locked is False


def test_locks_after_min_observations():
    d, _ = _trained(0)
    assert d.locked and np.isfinite(d.threshold)


def test_low_false_alarm_rate_on_clean_data():
    flags = n = 0
    for seed in range(10):
        d, rng = _trained(seed)
        for _ in range(200):
            flags += d.update(_obs(rng))["anomaly_detected"]
            n += 1
    assert flags / n < 0.02


def test_detects_small_sustained_shift_that_per_step_tests_miss():
    for seed in range(10):
        d, rng = _trained(seed)
        hit = False
        for _ in range(30):
            hit |= d.update(_obs(rng, shift=np.array([1.0, 0.0, 0.0])))["anomaly_detected"]
        assert hit, seed


def test_detects_large_abrupt_shift_immediately():
    d, rng = _trained(0)
    assert d.update(_obs(rng, shift=np.array([10.0, 10.0, 10.0])))["anomaly_detected"]


def test_reference_is_frozen_not_absorbing_the_attack():
    d, rng = _trained(1)
    for _ in range(300):  # long sustained shift must keep alarming, not become normal
        r = d.update(_obs(rng, shift=np.array([1.5, 0.0, 0.0])))
    assert r["anomaly_detected"] is True


def test_reanchor_relearns_reference():
    d, rng = _trained(2)
    d.reanchor()
    assert d.locked is False
    for _ in range(100):  # new normal is shifted
        d.update(_obs(rng, shift=np.array([5.0, 5.0, 5.0])))
    assert d.locked
    assert d.update(_obs(rng, shift=np.array([5.0, 5.0, 5.0])))["anomaly_detected"] is False


def test_constant_metric_does_not_crash():
    d = TemporalShiftDetector(min_observations=30)
    for i in range(60):
        r = d.update({"a": 1.0, "b": float(i % 3)})
    assert "anomaly_detected" in r


class TestHybridIntegration:
    @staticmethod
    def _sample(rng, shift=0.0):
        return {"a": float(rng.normal(100, 5) + shift * 5), "b": float(rng.normal(50, 3))}

    def test_off_by_default(self):
        det = HybridDetectorPipeline("t")
        assert det.temporal_detector is None
        det.ingest_metrics(1.0, {"a": 1.0, "b": 2.0})
        assert det.detect_anomalies()["temporal_detection"] is None

    def test_flags_sustained_shift_when_enabled(self):
        rng = np.random.default_rng(0)
        det = HybridDetectorPipeline("t", temporal_shift=True)
        for i in range(150):
            det.ingest_metrics(1000.0 + i, self._sample(rng))
        flagged = False
        for i in range(30):
            det.ingest_metrics(2000.0 + i, self._sample(rng, shift=1.0))
            r = det.detect_anomalies()
            step_flag = bool(r["temporal_detection"] and r["temporal_detection"]["anomaly_detected"])
            if step_flag:
                assert r["anomaly_detected"] is True
            flagged |= step_flag
        assert flagged
