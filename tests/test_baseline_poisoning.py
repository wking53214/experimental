import numpy as np
import pytest

from src.governance.baseline_check import baseline_statistics, check_baseline
from src.governance.calibrated_detector import CalibratedMahalanobisDetector

rng0 = np.random.default_rng(0)
COV = 0.4 * np.ones((5, 5)) + 0.6 * np.eye(5)


def clean(n=200, seed=0):
    return np.random.default_rng(seed).multivariate_normal(np.zeros(5), COV, size=n)


def contaminated(frac, seed=0):
    X = clean(200, seed)
    idx = np.random.default_rng(seed + 1).choice(200, int(frac * 200), replace=False)
    X[idx, 0] += 3.0
    return X


def test_clean_baselines_are_rarely_flagged():
    flagged = sum(check_baseline(clean(seed=s), sims=100, seed=s)["suspicious"] for s in range(40))
    assert flagged <= 8  # nominal 5% over the union; allow slack for 40 trials


@pytest.mark.parametrize("frac", [0.10, 0.20])
def test_moderate_contamination_is_often_flagged_but_not_always(frac):
    # measured at a 5% total false-flag budget: about 13/15 at 10% and 11/15 at 20% here
    hits = sum(check_baseline(contaminated(frac, s), sims=100, seed=s)["suspicious"] for s in range(15))
    assert 9 <= hits


def test_a_ramp_is_flagged_by_the_half_statistic():
    X = clean(seed=3)
    X[:, 0] += np.linspace(0.0, 3.0, 200)
    r = check_baseline(X, sims=150)
    assert r["suspicious"] and "half" in r["flags"]


def test_known_blind_spot_variance_inflation_is_not_flagged():
    """Documented limit: widening the spread looks like a normal, noisier system."""
    X = clean(seed=4)
    X[:, 0] *= 3.0
    hits = sum(check_baseline(np.column_stack([X[:, 0] * 1.0, clean(seed=4)[:, 1:]]), sims=100, seed=s)["suspicious"]
               for s in range(5))
    assert hits <= 2


def test_statistics_ignore_constant_columns():
    X = np.column_stack([clean(seed=5), np.full(200, 7.0)])
    assert set(baseline_statistics(X)) == {"tail", "shift", "half"}


def test_require_clean_refuses_contaminated_baselines_and_accepts_most_clean_ones():
    def refused(X):
        try:
            CalibratedMahalanobisDetector().fit(X, require_clean=True)
            return False
        except ValueError as e:
            assert "contaminated" in str(e)
            return True
    assert sum(refused(clean(seed=s)) for s in range(10)) <= 3
    assert sum(refused(contaminated(0.1, s)) for s in range(10)) >= 6


def test_trimmed_fit_helps_against_ten_percent_contamination():
    """Measured: +15 to +20 points of detection at 10% contamination across covariances. At 20%
    the gain depends on the covariance (7 to 29 points) and at 40% there is none, so only the
    10% case is asserted."""
    hits = {}
    for name, trim in (("plain", 0.0), ("trim", 0.25)):
        n = 0
        for seed in range(40):
            d = CalibratedMahalanobisDetector(target_fpr=0.01, trim=trim).fit(contaminated(0.10, seed))
            r = np.random.default_rng(100 + seed)
            xs = r.multivariate_normal(np.zeros(5), COV, size=20)
            xs[:, 0] += 3.0
            n += any(d.process(x) for x in xs)
        hits[name] = n
    assert hits["trim"] > hits["plain"]


def test_trimmed_fit_keeps_its_false_alarm_rate_on_clean_data():
    d = CalibratedMahalanobisDetector(target_fpr=0.01, trim=0.25).fit(clean(400, 7))
    r = np.random.default_rng(8).multivariate_normal(np.zeros(5), COV, size=4000)
    assert np.mean([d.process(x) for x in r]) < 0.04


def test_trim_validation():
    with pytest.raises(ValueError):
        CalibratedMahalanobisDetector(trim=0.6)


def _feed(g, X, names=("a", "b", "c", "d", "e")):
    for t, row in enumerate(X):
        g.ingest_metrics("svc", float(t), dict(zip(names, map(float, row))))


def test_governor_records_a_clean_baseline_check_and_queues_a_contaminated_one(tmp_path):
    from src.governance.governor import Governor
    g = Governor(store_path=str(tmp_path / "a"), baseline_check_at=200)
    _feed(g, clean(200, 11))
    entry = g.audit.find("baseline_check")[-1]["payload"]
    assert entry["boundary_id"] == "svc" and "suspicious" in entry
    g2 = Governor(store_path=str(tmp_path / "b"), baseline_check_at=200)
    _feed(g2, contaminated(0.10, 3))
    assert g2.audit.find("baseline_check") and g2.baseline_reviews, "10% contamination should be queued"
    _feed(g2, clean(50, 12))  # logged once, not repeatedly
    assert len(g2.audit.find("baseline_check")) == 1


def test_baseline_check_is_off_by_default(tmp_path):
    from src.governance.governor import Governor
    g = Governor(store_path=str(tmp_path))
    _feed(g, contaminated(0.2, 1))
    assert not g.audit.find("baseline_check") and g.baseline_reviews == []
