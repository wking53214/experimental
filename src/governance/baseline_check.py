"""
Is this baseline suspiciously contaminated? (docs/BASELINE_POISONING.md, threat T2)

An attacker present while the baseline is learned can teach the detector that their later attack is
normal. Three statistics, each compared with what clean Gaussian data of the same size gives
(simulated, so thresholds depend on n and the number of metrics):

  tail   share of baseline points beyond the chi-square 99% cutoff under a robust (trimmed) fit
         -> catches contamination up to about 20% of the window
  shift  distance between the ordinary mean and the robust mean -> same family
  half   distance between the first-half and second-half means -> catches a slow ramp

What it cannot catch (measured): variance inflation, and contamination of about 40% or more, where
the poisoned data is the majority and looks like the baseline. It assumes clean data is roughly
Gaussian and stationary, so on real, drifting telemetry expect false flags. A flag means "a person
should look at this window before it becomes the baseline", not "attack".
"""
from __future__ import annotations

from statistics import NormalDist

import numpy as np


def _robust_fit(X, trim=0.25, iters=5):
    mu, cov = X.mean(0), np.cov(X, rowvar=False)
    for _ in range(iters):
        inv = np.linalg.pinv(cov)
        d = np.sqrt(np.einsum("ij,jk,ik->i", X - mu, inv, X - mu))
        keep = d <= np.quantile(d, 1.0 - trim)
        mu, cov = X[keep].mean(0), np.cov(X[keep], rowvar=False)
    inv = np.linalg.pinv(cov)
    d = np.sqrt(np.einsum("ij,jk,ik->i", X - mu, inv, X - mu))
    return d, mu


def baseline_statistics(X) -> dict:
    X = np.asarray(X, dtype=float)
    X = X[:, X.std(0) > 1e-9]
    n, k = X.shape
    d, mu_r = _robust_fit(X)
    d2 = d ** 2 / (np.median(d ** 2) / (k * (1.0 - 2.0 / (9.0 * k)) ** 3))
    # chi-square 99% cutoff, Wilson-Hilferty
    cut = k * (1.0 - 2.0 / (9.0 * k) + NormalDist().inv_cdf(0.99) * (2.0 / (9.0 * k)) ** 0.5) ** 3
    inv = np.linalg.pinv(np.cov(X, rowvar=False))
    h = n // 2
    gap = X[:h].mean(0) - X[h:].mean(0)
    shift = X.mean(0) - mu_r
    return {"tail": float(np.mean(d2 > cut)),
            "shift": float(np.sqrt(shift @ inv @ shift)),
            "half": float(np.sqrt(gap @ inv @ gap))}


def check_baseline(X, alpha: float = 0.05, sims: int = 300, seed: int = 0) -> dict:
    """Compare a baseline's statistics with clean Gaussian data of the same shape.

    alpha is the total false-flag rate on clean Gaussian data, split over the three statistics.
    Returns {"suspicious": bool, "flags": [names], "statistics": {...}, "thresholds": {...}}.
    """
    X = np.asarray(X, dtype=float)
    Xv = X[:, X.std(0) > 1e-9]
    n, k = Xv.shape
    rng = np.random.default_rng(seed)
    sim = [baseline_statistics(rng.standard_normal((n, k))) for _ in range(sims)]
    per = 1.0 - alpha / 3.0
    thresholds = {name: float(np.quantile([s[name] for s in sim], per)) for name in ("tail", "shift", "half")}
    stats = baseline_statistics(Xv)
    flags = [name for name in thresholds if stats[name] > thresholds[name]]
    return {"suspicious": bool(flags), "flags": flags, "statistics": stats, "thresholds": thresholds}
