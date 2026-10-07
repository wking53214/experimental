"""
Phase 9F: Temporal shift detection (multivariate EWMA on a frozen reference).

The Phase 9A/9B detectors judge one observation at a time, and 9A's model keeps
learning from every observation it sees. Evolved attacks exploit both: a small shift
sustained over many steps never crosses a per-step threshold, and the model absorbs
it as "normal" while it runs.

This layer fixes both. It whitens observations with a reference distribution that is
frozen once learned, and accumulates evidence over time with an exponentially weighted
moving average (MEWMA). A persistent shift too small to alarm on any single step
still drives the statistic over its threshold.

Limitation: the reference is frozen, so legitimate long-term drift will eventually
alarm. Call reanchor() (e.g. when ConceptDriftDetector re-anchors) to relearn it.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


class TemporalShiftDetector:
    def __init__(self, min_observations: int = 100, smoothing: float = 0.2,
                 target_step_fpr: float = 0.005, seed: int = 12345,
                 dispersion: bool = True, dispersion_smoothing: float = 0.2):
        """
        Args:
            min_observations: clean observations used to learn the frozen reference
            smoothing: EWMA weight on the newest observation (lower = longer memory,
                better for small persistent shifts, slower for large sudden ones)
            target_step_fpr: false-alarm rate per step on clean data; sets the threshold
            seed: seed for the threshold calibration simulation
            dispersion: also track the energy of the whitened observations, which catches
                changes that leave the means alone: wider or narrower spread, oscillation,
                and broken correlations between metrics. The false-alarm budget is split
                between the two statistics.
            dispersion_smoothing: EWMA weight for the dispersion statistic
        """
        if not 0.0 < smoothing <= 1.0:
            raise ValueError("smoothing must be in (0, 1]")
        self.min_observations = min_observations
        self.smoothing = smoothing
        self.target_step_fpr = target_step_fpr
        self.seed = seed
        self.dispersion = dispersion
        self.dispersion_smoothing = dispersion_smoothing
        self.dispersion_threshold: float = float("inf")
        self._d: Optional[float] = None
        self._k = 0

        self.metric_names: Optional[List[str]] = None
        self._buffer: List[np.ndarray] = []
        self.locked = False
        self.mean: Optional[np.ndarray] = None
        self.whiten: Optional[np.ndarray] = None
        self.threshold: float = float("inf")
        self._z: Optional[np.ndarray] = None
        self.observation_count = 0

    def reanchor(self, observations: Optional[List[Dict[str, float]]] = None) -> None:
        """Replace the frozen reference.

        With at least min_observations recent observations, the reference is rebuilt
        from them immediately (no blind period). Otherwise it is discarded and relearned
        from the next min_observations, during which nothing alarms.
        """
        self._z = None
        self._d = None
        if observations is not None and len(observations) >= self.min_observations:
            names = self.metric_names or list(observations[-1].keys())
            self._buffer = [np.array([o.get(m, 0.0) for m in names], dtype=float)
                            for o in observations[-self.min_observations:]]
            self._lock()
            return
        self._buffer = []
        self.locked = False

    def update(self, observation: Dict[str, float]) -> Dict:
        """Ingest one observation; return the detection result for it."""
        if self.metric_names is None:
            self.metric_names = list(observation.keys())
        x = np.array([observation.get(m, 0.0) for m in self.metric_names], dtype=float)
        self.observation_count += 1

        if not self.locked:
            self._buffer.append(x)
            if len(self._buffer) >= self.min_observations:
                self._lock()
            return {"anomaly_detected": False, "statistic": 0.0,
                    "threshold": self.threshold, "anomaly_score": 0.0,
                    "reason": f"Learning reference ({len(self._buffer)}/{self.min_observations})"}

        stat, dstat = self._step(x)
        mean_hit = bool(stat > self.threshold)
        disp_hit = bool(self.dispersion and abs(dstat) > self.dispersion_threshold)
        detected = mean_hit or disp_hit
        ratio = max(stat / self.threshold,
                    abs(dstat) / self.dispersion_threshold if self.dispersion else 0.0)
        reasons = []
        if mean_hit:
            reasons.append("Sustained shift from reference")
        if disp_hit:
            reasons.append("Spread/correlation change from reference ("
                           + ("wider" if dstat > 0 else "narrower") + ")")
        return {"anomaly_detected": detected, "statistic": float(stat),
                "threshold": float(self.threshold),
                "dispersion_statistic": float(dstat),
                "dispersion_threshold": float(self.dispersion_threshold),
                "anomaly_score": float(min(1.0, ratio / 2.0)) if detected else 0.0,
                "reason": " | ".join(reasons) if reasons else "Normal"}

    def _fit(self, data: np.ndarray):
        mean = data.mean(axis=0)
        cov = np.cov(data, rowvar=False)
        cov = np.atleast_2d(cov)
        vals, vecs = np.linalg.eigh(cov)
        floor = max(vals.max() * 1e-8, 1e-12)
        vals = np.maximum(vals, floor)
        whiten = (vecs / np.sqrt(vals)).T  # rows scale each principal axis to unit variance
        return mean, whiten

    def _step(self, x: np.ndarray):
        lam = self.smoothing
        z = self.whiten @ (x - self.mean)
        self._z = lam * z + (1.0 - lam) * (self._z if self._z is not None else 0.0)
        mean_stat = float(self._z @ self._z) / (lam / (2.0 - lam))

        ld = self.dispersion_smoothing
        k = self._k
        self._d = ld * float(z @ z) + (1.0 - ld) * (self._d if self._d is not None else float(k))
        disp_stat = (self._d - k) / np.sqrt(2.0 * k * ld / (2.0 - ld))
        return mean_stat, float(disp_stat)

    def _lock(self) -> None:
        data = np.array(self._buffer)
        self._k = data.shape[1]
        self.mean, self.whiten = self._fit(data)
        self._z = None
        self._d = None
        self.threshold, self.dispersion_threshold = self._calibrate(data.shape[1], len(data))
        self.locked = True

    def _calibrate(self, k: int, n_ref: int, replicates: int = 8, steps: int = 400):
        """Thresholds giving ~target_step_fpr on clean data (split between the two
        statistics), including the error from estimating the reference from n_ref
        observations."""
        rng = np.random.default_rng(self.seed)
        lam, ld = self.smoothing, self.dispersion_smoothing
        scale = lam / (2.0 - lam)
        dscale = np.sqrt(2.0 * k * ld / (2.0 - ld))
        mean_stats, disp_stats = [], []
        for _ in range(replicates):
            ref = rng.standard_normal((n_ref, k))
            mean, whiten = self._fit(ref)
            z_ewma = np.zeros(k)
            d = float(k)
            for t in range(steps):
                z = whiten @ (rng.standard_normal(k) - mean)
                z_ewma = lam * z + (1 - lam) * z_ewma
                d = ld * float(z @ z) + (1 - ld) * d
                if t >= 50:
                    mean_stats.append(float(z_ewma @ z_ewma) / scale)
                    disp_stats.append(abs(d - k) / dscale)
        budget = self.target_step_fpr / 2.0 if self.dispersion else self.target_step_fpr
        return (float(np.quantile(mean_stats, 1.0 - budget)),
                float(np.quantile(disp_stats, 1.0 - budget)) if self.dispersion else float("inf"))
