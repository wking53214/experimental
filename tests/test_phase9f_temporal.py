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


class TestDriftWiring:
    @staticmethod
    def _run(seed, steps, shift_fn, **kw):
        rng = np.random.default_rng(seed)
        det = HybridDetectorPipeline("w", temporal_shift=True, **kw)
        for i in range(150):
            det.ingest_metrics(1000.0 + i, {"a": float(rng.normal(100, 5)), "b": float(rng.normal(50, 3))})
        flags = []
        for i in range(steps):
            da, db = shift_fn(i)
            det.ingest_metrics(2000.0 + i, {"a": float(rng.normal(100 + da, 5)), "b": float(rng.normal(50 + db, 3))})
            flags.append(bool(det.detect_anomalies()["temporal_detection"]["anomaly_detected"]))
        return det, np.array(flags)

    @staticmethod
    def _drift(i):  # legitimate drift: +2 sigma over 200 steps, then plateau
        f = min(i, 200) / 200
        return 10.0 * f, 6.0 * f

    def test_following_drift_cuts_false_alarms_vs_frozen(self):
        wired = frozen = 0.0
        for seed in range(3):
            wired += self._run(seed, 400, self._drift)[1][300:].mean()
            frozen += self._run(seed, 400, self._drift, reanchor_on_drift=False)[1][300:].mean()
        assert frozen / 3 > 0.9
        assert wired / 3 < frozen / 3 - 0.3

    def test_abrupt_shift_held_until_acknowledged(self):
        shift = lambda i: (15.0, 0.0) if i >= 20 else (0.0, 0.0)
        det, flags = self._run(0, 250, shift)
        assert flags[150:].mean() > 0.9  # not absorbed
        assert det.drift_detector.get_drift_status()["drift_type"] == "sudden"
        det.acknowledge_shift()
        rng = np.random.default_rng(9)
        post = []
        for i in range(60):
            det.ingest_metrics(9000.0 + i, {"a": float(rng.normal(115, 5)), "b": float(rng.normal(50, 3))})
            post.append(det.detect_anomalies()["temporal_detection"]["anomaly_detected"])
        assert np.mean(post[10:]) < 0.2

    def test_reanchor_from_recent_observations_has_no_blind_period(self):
        d, rng = _trained(0)
        recent = [_obs(rng, shift=np.array([5.0, 5.0, 5.0])) for _ in range(100)]
        d.reanchor(recent)
        assert d.locked
        assert d.update(_obs(rng, shift=np.array([5.0, 5.0, 5.0])))["anomaly_detected"] is False
        assert d.update(_obs(rng, shift=np.array([15.0, 15.0, 15.0])))["anomaly_detected"] is True

    def test_not_wired_when_temporal_disabled(self):
        det = HybridDetectorPipeline("w")
        assert det.drift_detector is None
        det.acknowledge_shift()  # no-op, must not raise


class TestDispersionStatistic:
    """Changes that leave the means alone: spread, oscillation, broken correlations."""
    L = np.linalg.cholesky(np.array([[1.0, 0.8, 0.0], [0.8, 1.0, 0.0], [0.0, 0.0, 1.0]]))

    def _draw(self, rng):
        return self.L @ rng.standard_normal(3)  # a and b correlated at 0.8

    def _trained(self, seed, **kw):
        rng = np.random.default_rng(seed)
        d = TemporalShiftDetector(**kw)
        for _ in range(100):
            v = self._draw(rng)
            d.update({"a": float(v[0]), "b": float(v[1]), "c": float(v[2])})
        return d, rng

    def _detect_rate(self, attack, seeds=8, steps=40, **kw):
        hits = 0
        for s in range(seeds):
            d, rng = self._trained(s, **kw)
            hit = False
            for i in range(steps):
                v = self._draw(rng)
                v = attack(v, i, rng)
                hit |= d.update({"a": float(v[0]), "b": float(v[1]), "c": float(v[2])})["anomaly_detected"]
            hits += hit
        return hits / seeds

    def test_clean_false_alarm_rate_stays_low(self):
        flags = n = 0
        for s in range(10):
            d, rng = self._trained(s)
            for _ in range(200):
                v = self._draw(rng)
                flags += d.update({"a": float(v[0]), "b": float(v[1]), "c": float(v[2])})["anomaly_detected"]
                n += 1
        assert flags / n < 0.02

    def test_detects_variance_inflation(self):
        def attack(v, i, rng):
            v = v.copy(); v[0] *= 2.5; return v
        assert self._detect_rate(attack) >= 0.85

    def test_detects_oscillation_with_zero_mean(self):
        def attack(v, i, rng):
            v = v.copy(); v[0] += (2.5 if i % 2 == 0 else -2.5); return v
        assert self._detect_rate(attack) >= 0.85

    def test_detects_broken_correlation_with_intact_marginals(self):
        def attack(v, i, rng):
            v = v.copy(); v[1] = rng.standard_normal(); return v  # same marginal, no link to a
        assert self._detect_rate(attack) >= 0.85

    def test_reports_which_statistic_fired(self):
        d, rng = self._trained(0)
        reasons = set()
        for i in range(40):
            v = self._draw(rng); v[0] += (3.0 if i % 2 == 0 else -3.0)
            r = d.update({"a": float(v[0]), "b": float(v[1]), "c": float(v[2])})
            if r["anomaly_detected"]:
                reasons.add(r["reason"])
        assert any("Spread/correlation" in r for r in reasons)

    def test_can_be_disabled(self):
        d, _ = self._trained(0, dispersion=False)
        assert d.dispersion_threshold == float("inf")
