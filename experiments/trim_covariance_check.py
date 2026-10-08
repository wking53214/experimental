"""
Does the robust (trimmed) fit help regardless of the metrics' covariance? Contamination of one
metric by +3 sd at 0%, 10% and 20% of a 200-step baseline; probe = +3 sd on that metric for 20 steps.
Reports detection and the chance floor (alarm in a no-attack window) for the plain and trim=0.25 fits.
Run:  PYTHONPATH=. python -m experiments.trim_covariance_check
"""
import numpy as np

from experiments.phase9_end_to_end import COV as C9, SD
from src.governance.calibrated_detector import CalibratedMahalanobisDetector as C

COVS = {"phase9 (mixed corr)": C9 / np.outer(SD, SD),
        "equicorr 0.4": 0.4 * np.ones((5, 5)) + 0.6 * np.eye(5),
        "identity": np.eye(5)}


def trial(cov, frac, trim, seed):
    rng = np.random.default_rng(seed)
    X = rng.multivariate_normal(np.zeros(5), cov, size=200)
    idx = rng.choice(200, int(frac * 200), replace=False)
    X[idx, 0] += 3.0
    d = C(target_fpr=0.01, trim=trim).fit(X)
    xs = rng.multivariate_normal(np.zeros(5), cov, size=20)
    xs[:, 0] += 3.0
    ctrl = rng.multivariate_normal(np.zeros(5), cov, size=20)
    return any(d.process(x) for x in xs), any(d.process(x) for x in ctrl)


if __name__ == "__main__":
    print(f"{'covariance':22s} contam  plain det/chance   trim25 det/chance")
    for name, cov in COVS.items():
        for frac in (0.0, 0.1, 0.2):
            row = []
            for trim in (0.0, 0.25):
                r = [trial(cov, frac, trim, s) for s in range(80)]
                row.append((np.mean([a for a, _ in r]), np.mean([b for _, b in r])))
            print(f"{name:22s} {frac:5.0%}   {row[0][0]:.2f} / {row[0][1]:.2f}        {row[1][0]:.2f} / {row[1][1]:.2f}")
