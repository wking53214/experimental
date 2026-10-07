import numpy as np

from experiments.baselines import MahalanobisBaseline, ZScoreBaseline

NAMES = ["a", "b", "c"]


def _stream(det, rng, n, shift=0.0):
    flags = 0
    for _ in range(n):
        x = rng.normal(0, 1, 3)
        x[0] += shift
        flags += det.ingest(dict(zip(NAMES, x)))
    return flags


def test_baselines_quiet_on_clean_and_loud_on_shift():
    for cls in (ZScoreBaseline, MahalanobisBaseline):
        rng = np.random.default_rng(0)
        det = cls(NAMES, fit_after=500)
        assert _stream(det, rng, 500) == 0  # no alarms while fitting
        assert _stream(det, rng, 2000) <= 20  # about 0.1% nominal, allow slack
        assert _stream(det, rng, 50, shift=8.0) >= 45


def test_baselines_ignore_constant_columns():
    rng = np.random.default_rng(1)
    for cls in (ZScoreBaseline, MahalanobisBaseline):
        det = cls(NAMES, fit_after=200)
        for _ in range(200):
            det.ingest({"a": rng.normal(), "b": rng.normal(), "c": 5.0})
        assert not det.ingest({"a": 0.0, "b": 0.0, "c": 5.0})
