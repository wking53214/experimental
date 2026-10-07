"""
Plain baselines to compare the project's detectors against.

Both fit once on a clean baseline and stay frozen. Each uses the same nominal false-alarm rate
(0.1% per step under a Gaussian model) as the calibrated generative detector, so differences
come from the method and not from a looser or tighter threshold.

  ZScoreBaseline        : alarm if any metric's |z| exceeds a Bonferroni-corrected cutoff.
  MahalanobisBaseline   : alarm if the Mahalanobis distance exceeds the chi-square cutoff;
                          ridge-regularised covariance, no online updates.
"""
from __future__ import annotations

from statistics import NormalDist

import numpy as np

from src.governance.multivariate import calibrated_mahalanobis_threshold


class _Base:
    def __init__(self, metrics, fit_after=120, family_alpha=0.001, quantile_z=3.0902):
        self.metrics, self.fit_after, self.family_alpha = list(metrics), fit_after, family_alpha
        self.quantile_z = quantile_z  # normal quantile of the nominal per-step false-alarm rate
        self._rows, self.fitted = [], False

    def ingest(self, obs) -> bool:
        x = np.array([obs[m] for m in self.metrics], dtype=float)
        if not self.fitted:
            self._rows.append(x)
            if len(self._rows) >= self.fit_after:
                self.fit(np.vstack(self._rows))
            return False
        return bool(self.score(x) > self.threshold)

    def fit(self, X):
        raise NotImplementedError


class ZScoreBaseline(_Base):
    def fit(self, X):
        self.mu = X.mean(0)
        self.sd = X.std(0)
        self.keep = self.sd > 1e-9
        k = int(self.keep.sum())
        self.threshold = NormalDist().inv_cdf(1 - self.family_alpha / (2 * max(k, 1)))
        self.fitted = True

    def score(self, x):
        z = np.abs((x - self.mu) / np.where(self.keep, self.sd, 1.0))
        return float(z[self.keep].max()) if self.keep.any() else 0.0


class MahalanobisBaseline(_Base):
    def fit(self, X):
        self.mu = X.mean(0)
        sd = X.std(0)
        self.keep = sd > 1e-9
        Xk = X[:, self.keep] - self.mu[self.keep]
        cov = np.atleast_2d(np.cov(Xk, rowvar=False))
        cov = cov + 1e-6 * np.trace(cov) / cov.shape[0] * np.eye(cov.shape[0])
        self.prec = np.linalg.inv(cov)
        self.threshold = calibrated_mahalanobis_threshold(int(self.keep.sum()), self.quantile_z)
        self.fitted = True

    def score(self, x):
        d = (x - self.mu)[self.keep]
        return float(np.sqrt(max(d @ self.prec @ d, 0.0)))
