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
                 target_step_fpr: float = 0.005, seed: int = 12345):
        """
        Args:
            min_observations: clean observations used to learn the frozen reference
            smoothing: EWMA weight on the newest observation (lower = longer memory,
                better for small persistent shifts, slower for large sudden ones)
            target_step_fpr: false-alarm rate per step on clean data; sets the threshold
            seed: seed for the threshold calibration simulation
        """
        if not 0.0 < smoothing <= 1.0:
            raise ValueError("smoothing must be in (0, 1]")
        self.min_observations = min_observations
        self.smoothing = smoothing
        self.target_step_fpr = target_step_fpr
        self.seed = seed

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

        stat = self._step(x)
        detected = bool(stat > self.threshold)
        return {"anomaly_detected": detected, "statistic": float(stat),
                "threshold": float(self.threshold),
                "anomaly_score": float(min(1.0, stat / (2.0 * self.threshold))) if detected else 0.0,
                "reason": "Sustained shift from reference" if detected else "Normal"}

    def _fit(self, data: np.ndarray):
        mean = data.mean(axis=0)
        cov = np.cov(data, rowvar=False)
        cov = np.atleast_2d(cov)
        vals, vecs = np.linalg.eigh(cov)
        floor = max(vals.max() * 1e-8, 1e-12)
        vals = np.maximum(vals, floor)
        whiten = (vecs / np.sqrt(vals)).T  # rows scale each principal axis to unit variance
        return mean, whiten

    def _step(self, x: np.ndarray) -> float:
        lam = self.smoothing
        z = self.whiten @ (x - self.mean)
        self._z = lam * z + (1.0 - lam) * (self._z if self._z is not None else 0.0)
        return float(self._z @ self._z) / (lam / (2.0 - lam))

    def _lock(self) -> None:
        data = np.array(self._buffer)
        self.mean, self.whiten = self._fit(data)
        self._z = None
        self.threshold = self._calibrate(data.shape[1], len(data))
        self._z = None
        self.locked = True

    def _calibrate(self, k: int, n_ref: int, replicates: int = 8, steps: int = 400) -> float:
        """Threshold giving ~target_step_fpr on clean data, including the error from
        estimating the reference from only n_ref observations."""
        rng = np.random.default_rng(self.seed)
        lam = self.smoothing
        scale = lam / (2.0 - lam)
        stats = []
        for _ in range(replicates):
            ref = rng.standard_normal((n_ref, k))
            mean, whiten = self._fit(ref)
            z = np.zeros(k)
            for t in range(steps):
                z = lam * (whiten @ (rng.standard_normal(k) - mean)) + (1 - lam) * z
                if t >= 50:
                    stats.append(float(z @ z) / scale)
        return float(np.quantile(stats, 1.0 - self.target_step_fpr))
