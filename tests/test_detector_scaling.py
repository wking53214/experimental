import numpy as np

from src.governance.metrics import DetectorPipeline, required_anomalous_metrics


def test_required_count_is_two_for_small_systems_and_grows_with_metrics():
    assert all(required_anomalous_metrics(n) == 2 for n in range(1, 8))
    ks = [required_anomalous_metrics(n) for n in (10, 20, 30, 38)]
    assert ks == sorted(ks) and ks[-1] > 2


def _false_alarm_rate(n_metrics, seed=0):
    rng = np.random.default_rng(seed)
    names = [f"m{i}" for i in range(n_metrics)]
    d = DetectorPipeline("b", 100, baseline_observations=200)
    for t in range(200):
        d.ingest_metrics(float(t), dict(zip(names, rng.normal(0, 1, n_metrics))))
    flags = 0
    for t in range(200, 1200):
        d.ingest_metrics(float(t), dict(zip(names, rng.normal(0, 1, n_metrics))))
        flags += d.detect_anomalies()["anomaly_detected"]
    return flags / 1000


def test_false_alarms_stay_bounded_with_many_independent_metrics():
    # the old fixed "2 or more" rule gave about 40% at 30 metrics
    assert _false_alarm_rate(30) < 0.10


def test_baseline_waits_for_requested_observations():
    d = DetectorPipeline("b", 100, baseline_observations=60)
    for t in range(59):
        d.ingest_metrics(float(t), {"a": float(t % 3), "b": 1.0})
    assert not d.baseline_locked
    d.ingest_metrics(59.0, {"a": 1.0, "b": 1.0})
    assert d.baseline_locked
