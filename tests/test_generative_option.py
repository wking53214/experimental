import numpy as np
import pytest

from src.governance.calibrated_detector import CalibratedMahalanobisDetector
from src.governance.governor import Governor


def _feed(g, n, rng, shift=0.0, start=0):
    alarms = 0
    for t in range(start, start + n):
        x = rng.normal(0, 1, 6)
        x[0] += shift
        det, _ = g.ingest_metrics("b", float(t), {f"m{i}": float(v) for i, v in enumerate(x)})
        alarms += bool(det["anomaly_detected"])
    return alarms


def test_default_detection_is_generative_and_bad_value_rejected(tmp_path):
    assert Governor(store_path=str(tmp_path)).detection == "generative"
    assert Governor(store_path=str(tmp_path), detection="hybrid").detection == "hybrid"
    with pytest.raises(ValueError):
        Governor(store_path=str(tmp_path), detection="nope")


def test_generative_option_is_quiet_on_clean_data_and_catches_a_shift(tmp_path):
    g = Governor(store_path=str(tmp_path), detection="generative")
    rng = np.random.default_rng(0)
    _feed(g, 200, rng)
    assert _feed(g, 500, rng, start=200) < 40
    assert _feed(g, 30, rng, shift=10.0, start=700) >= 25


def test_calibrated_detector_holds_its_target_on_gaussian_data_and_flags_shifts():
    rng = np.random.default_rng(0)
    d = CalibratedMahalanobisDetector(target_fpr=0.02).fit(rng.normal(size=(3000, 5)))
    clean = np.mean([d.process(x) for x in rng.normal(size=(5000, 5))])
    assert 0.005 < clean < 0.05
    assert all(d.process(np.array([8.0, 0, 0, 0, 0])) for _ in range(20))


def test_calibrated_detector_ignores_constant_columns_and_validates_input():
    rng = np.random.default_rng(1)
    X = np.column_stack([rng.normal(size=500), np.full(500, 3.0), rng.normal(size=500)])
    d = CalibratedMahalanobisDetector().fit(X)
    assert not d.process([0.0, 99.0, 0.0])  # the constant column is dropped, not scored
    with pytest.raises(ValueError):
        CalibratedMahalanobisDetector(target_fpr=1.5)
    with pytest.raises(ValueError):
        CalibratedMahalanobisDetector().fit(X[:10])


def test_adaptive_calibrated_detector_still_flags_a_gross_outlier():
    rng = np.random.default_rng(2)
    d = CalibratedMahalanobisDetector(decay=0.01, reject_mult=2.0).fit(rng.normal(size=(2000, 4)))
    for x in rng.normal(size=(500, 4)):
        d.process(x)
    assert all(d.process(np.array([12.0, 0, 0, 0])) for _ in range(50))  # not learned as normal
