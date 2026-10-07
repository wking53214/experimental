"""
Calibrated Mahalanobis detector.

Why this exists: on real telemetry (Server Machine Dataset) every threshold taken from a Gaussian
formula false-alarmed 10 to 80 times more often than intended, because real metrics are heavy-tailed
and drift. Here the threshold is the empirical (1 - target_fpr) quantile of the detector's own scores
on the most recent part of the baseline, scored the same way it will run in service (before each
update). The mean and covariance can follow slow change (decay > 0) from steps that were NOT flagged,
so a persistent anomaly stays flagged instead of being learned.

Trade-off: with decay > 0 a change slower than the decay time constant (about 1 / decay steps) is
absorbed as normal, so a slow attack can be learned. decay = 0 is a frozen model.
"""
from __future__ import annotations

import numpy as np


class CalibratedMahalanobisDetector:
    def __init__(self, target_fpr: float = 0.01, decay: float = 0.0, holdout_fraction: float = 0.3,
                 recompute_every: int = 50, ridge: float = 1e-6, min_fit: int = 50,
                 reject_mult: float = 1.0):
        if not 0.0 < target_fpr < 1.0:
            raise ValueError("target_fpr must be in (0, 1)")
        self.target_fpr, self.decay = target_fpr, decay
        self.holdout_fraction, self.recompute_every = holdout_fraction, recompute_every
        self.ridge, self.min_fit = ridge, min_fit
        # adapt on steps scoring up to reject_mult x the alarm threshold: 1.0 learns only unflagged
        # steps (can get stuck after a level shift); larger values let the model follow a shift
        # while still refusing gross outliers
        self.reject_mult = reject_mult
        self.fitted = False
        self.threshold = float("inf")

    # ---- model, in the space of metrics that vary in the baseline ----
    def _set_model(self, X):
        self.mu = X.mean(0)
        self.cov = np.atleast_2d(np.cov(X, rowvar=False))
        self._refresh()

    def _refresh(self):
        d = self.cov.shape[0]
        reg = self.cov + self.ridge * max(np.trace(self.cov) / d, 1e-12) * np.eye(d)
        self.prec = np.linalg.pinv(reg)
        self._since = 0

    def _score(self, xr) -> float:
        d = xr - self.mu
        return float(np.sqrt(max(d @ self.prec @ d, 0.0)))

    def _adapt(self, xr):
        w = self.decay
        delta = xr - self.mu
        self.mu = self.mu + w * delta
        self.cov = (1.0 - w) * (self.cov + w * np.outer(delta, delta))
        self._since += 1
        if self._since >= self.recompute_every:
            self._refresh()

    # ---- API ----
    def fit(self, X) -> "CalibratedMahalanobisDetector":
        X = np.asarray(X, dtype=float)
        self.keep = X.std(0) > 1e-9
        X = X[:, self.keep]
        n = len(X)
        if n < self.min_fit:
            raise ValueError(f"need at least {self.min_fit} baseline observations")
        k = min(max(int(n * (1.0 - self.holdout_fraction)), self.min_fit // 2), n - 1)
        self._set_model(X[:k])
        scores = []
        for row in X[k:]:  # prequential: score first, then adapt, exactly as in service
            sc = self._score(row)
            scores.append(sc)
            if self.decay > 0 and sc <= self._cap(scores):
                self._adapt(row)
        self.threshold = float(np.quantile(scores, 1.0 - self.target_fpr))
        self.fitted = True
        return self

    def _cap(self, scores_so_far):
        # during calibration the threshold is not known yet: use the running quantile
        return self.reject_mult * float(np.quantile(scores_so_far, 1.0 - self.target_fpr))

    def score(self, x) -> float:
        return self._score(np.asarray(x, dtype=float)[self.keep])

    def process(self, x) -> bool:
        """Score one observation, report whether it is flagged, adapt if it was not."""
        if not self.fitted:
            raise RuntimeError("fit first")
        xr = np.asarray(x, dtype=float)[self.keep]
        sc = self._score(xr)
        flagged = sc > self.threshold
        if self.decay > 0 and sc <= self.reject_mult * self.threshold:
            self._adapt(xr)
        return bool(flagged)
