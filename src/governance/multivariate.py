"""
Phase 9A: Generative Anomaly Detection via Multivariate Gaussian Learning

M1 FIXED: empty observation / empty array guards before np.mean / np.std.
"""

import numpy as np
import time
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field


@dataclass
class GaussianStats:
    metric_names: List[str]
    mean: np.ndarray
    cov_matrix: np.ndarray
    observation_count: int
    min_eigenvalue: float = field(default=0.0)
    condition_number: float = field(default=0.0)
    is_singular: bool = field(default=False)


class MultivariateGaussian:
    def __init__(self, metric_names: List[str], regularization: float = 1e-6):
        self.metric_names = metric_names
        self.n_metrics = len(metric_names)
        self.regularization = regularization
        self.n = 0
        self.mean = np.zeros(self.n_metrics)
        self.M2 = np.zeros((self.n_metrics, self.n_metrics))
        self._cov_matrix = None
        self._cov_inverse = None
        self._cov_determinant = None
        self._is_valid = False

    def update(self, observation: Dict[str, float]) -> None:
        x = np.array([observation.get(name, 0.0) for name in self.metric_names])
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += np.outer(delta, delta2)
        self._is_valid = False

    def get_covariance(self) -> np.ndarray:
        if self._cov_matrix is None or not self._is_valid:
            if self.n < 2:
                self._cov_matrix = np.eye(self.n_metrics)
            else:
                raw_cov = self.M2 / max(1, self.n - 1)
                self._cov_matrix = raw_cov + self.regularization * np.eye(self.n_metrics)
        return self._cov_matrix

    def get_cov_inverse(self) -> Optional[np.ndarray]:
        if self._cov_inverse is None or not self._is_valid:
            cov = self.get_covariance()
            try:
                self._cov_inverse = np.linalg.inv(cov)
            except np.linalg.LinAlgError:
                self._cov_inverse = np.linalg.pinv(cov)
        return self._cov_inverse

    def get_determinant(self) -> float:
        if self._cov_determinant is None or not self._is_valid:
            cov = self.get_covariance()
            self._cov_determinant = max(1e-10, float(np.linalg.det(cov)))
        return self._cov_determinant

    def get_stats(self) -> GaussianStats:
        cov = self.get_covariance()
        try:
            eigenvalues = np.linalg.eigvalsh(cov)
            min_eig = float(np.min(eigenvalues))
            max_eig = float(np.max(eigenvalues))
            condition = max_eig / max(min_eig, 1e-10)
        except Exception:
            min_eig = 0.0
            condition = float("inf")
        return GaussianStats(
            metric_names=self.metric_names,
            mean=self.mean.copy(),
            cov_matrix=cov.copy(),
            observation_count=self.n,
            min_eigenvalue=min_eig,
            condition_number=condition,
            is_singular=min_eig < 1e-10,
        )

    def mahalanobis_distance(self, observation: Dict[str, float]) -> float:
        x = np.array([observation.get(name, 0.0) for name in self.metric_names])
        delta = x - self.mean
        cov_inv = self.get_cov_inverse()
        if cov_inv is None:
            return 0.0
        try:
            md_squared = float(delta @ cov_inv @ delta)
            return float(np.sqrt(max(0.0, md_squared)))
        except Exception:
            return 0.0

    def log_likelihood(self, observation: Dict[str, float]) -> float:
        md = self.mahalanobis_distance(observation)
        det = self.get_determinant()
        k = self.n_metrics
        return float(-0.5 * (k * np.log(2 * np.pi) + np.log(det) + md ** 2))


def calibrated_mahalanobis_threshold(n_metrics: int, quantile_z: float = 3.0902) -> float:
    """Mahalanobis distance exceeded by ~0.1% of normal observations in n dimensions.

    Wilson-Hilferty approximation of sqrt(chi-square 99.9th percentile, n dof).
    A fixed 3.0 is a one-dimensional 3-sigma rule and false-alarms often when n > 1.
    """
    k = float(max(1, n_metrics))
    c = 2.0 / (9.0 * k)
    return float((k * (1.0 - c + quantile_z * np.sqrt(c)) ** 3) ** 0.5)


class MahalanobisAnomalyScorer:
    def __init__(self, threshold: float = 3.0, saturation: float = 5.0):
        self.threshold = threshold
        self.saturation = saturation

    def score(self, mahalanobis_distance: float) -> float:
        if mahalanobis_distance < self.threshold:
            return 0.0
        if mahalanobis_distance >= self.saturation:
            return 1.0
        score = (mahalanobis_distance - self.threshold) / (self.saturation - self.threshold)
        return float(np.clip(score, 0.0, 1.0))


class InvariantLearner:
    def __init__(self, min_observations: int = 50):
        self.min_observations = min_observations
        self.observations: List[Dict[str, float]] = []
        self.invariants: Dict[Tuple[str, str], Dict] = {}

    def add_observation(self, observation: Dict[str, float]) -> None:
        self.observations.append(observation.copy())
        if len(self.observations) > self.min_observations * 2:
            self.observations = self.observations[-self.min_observations:]

    def learn_invariants(self) -> Dict[Tuple[str, str], Dict]:
        if len(self.observations) < self.min_observations:
            return {}
        metric_names = list(self.observations[0].keys())
        for m1 in metric_names:
            for m2 in metric_names:
                if m1 >= m2:
                    continue
                values1 = np.array([o.get(m1, 0.0) for o in self.observations], dtype=float)
                values2 = np.array([o.get(m2, 0.0) for o in self.observations], dtype=float)
                if values1.size == 0 or values2.size == 0:
                    continue
                if np.std(values1) < 1e-10 or np.std(values2) < 1e-10:
                    continue
                corr = np.corrcoef(values1, values2)[0, 1]
                if np.isnan(corr):
                    continue
                self.invariants[(m1, m2)] = {
                    "correlation": float(corr),
                    "strength": abs(float(corr)),
                    "direction": "positive" if corr > 0 else "negative",
                }
        return self.invariants

    def check_invariant_violation(self, observation: Dict[str, float]) -> Optional[Dict]:
        if not self.invariants:
            return None
        values = np.array(list(observation.values()), dtype=float)
        # M1: empty observation → no invariant check (avoids Mean of empty slice)
        if values.size == 0:
            return None
        mean_val = float(np.mean(values))
        for (m1, m2), invariant in self.invariants.items():
            if m1 not in observation or m2 not in observation:
                continue
            v1 = observation[m1]
            v2 = observation[m2]
            expected_corr = invariant["correlation"]
            if expected_corr > 0.5:
                if v1 > mean_val and v2 < mean_val:
                    return {
                        "type": "invariant_violation",
                        "metrics": (m1, m2),
                        "expected_correlation": expected_corr,
                        "severity": abs(expected_corr),
                    }
        return None


class GenerativeAnomalyDetector:
    def __init__(self, boundary_id: str, min_observations: int = 20,
                 mahalanobis_threshold: Optional[float] = None, regularization: float = 1e-6):
        # None: calibrate from the number of metrics when the first observation arrives.
        self.boundary_id = boundary_id
        self.min_observations = min_observations
        self.model: Optional[MultivariateGaussian] = None
        self._calibrate = mahalanobis_threshold is None
        self.scorer = MahalanobisAnomalyScorer(
            threshold=3.0 if mahalanobis_threshold is None else mahalanobis_threshold)
        self.invariant_learner = InvariantLearner(min_observations=min_observations)
        self.observation_count = 0
        self.model_locked = False
        self.regularization = regularization

    def required_observations(self) -> int:
        """Observations needed before the model is trusted: at least min_observations, and at least
        5 per metric. A d-dimensional covariance cannot be estimated from a handful of points:
        with 30 metrics, locking at 20 observations false-alarmed on 48% of the first 100 steps,
        and 5 per metric brings that to about 1%."""
        n_metrics = len(self.model.metric_names) if self.model is not None else 0
        return max(self.min_observations, 5 * n_metrics)

    def ingest_observation(self, observation: Dict[str, float]) -> None:
        if self.model is None:
            self.model = MultivariateGaussian(list(observation.keys()), regularization=self.regularization)
            if self._calibrate:
                thr = calibrated_mahalanobis_threshold(len(observation))
                self.scorer = MahalanobisAnomalyScorer(threshold=thr, saturation=thr + 2.0)
        self.model.update(observation)
        self.invariant_learner.add_observation(observation)
        self.observation_count += 1
        if self.observation_count >= self.required_observations() and not self.model_locked:
            self.model_locked = True
            self.invariant_learner.learn_invariants()

    def detect_anomaly(self, observation: Dict[str, float]) -> Dict:
        if self.model is None:
            return {"anomaly_detected": False, "anomaly_score": 0.0,
                    "mahalanobis_distance": 0.0, "reason": "Model not yet trained"}
        if not self.model_locked:
            return {"anomaly_detected": False, "anomaly_score": 0.0,
                    "mahalanobis_distance": 0.0,
                    "reason": f"Baseline learning ({self.observation_count}/{self.required_observations()})"}
        md = self.model.mahalanobis_distance(observation)
        anomaly_score = self.scorer.score(md)
        invariant_violation = self.invariant_learner.check_invariant_violation(observation)
        stats = self.model.get_stats()
        reasons = []
        if anomaly_score > 0.0:
            reasons.append(f"Multivariate anomaly (MD={md:.2f})")
        if invariant_violation:
            reasons.append(f"Invariant violation: {invariant_violation['metrics']}")
        return {
            "anomaly_detected": anomaly_score > 0.0,
            "anomaly_score": anomaly_score,
            "mahalanobis_distance": md,
            "log_likelihood": self.model.log_likelihood(observation),
            "invariant_violation": invariant_violation,
            "model_condition": stats.condition_number,
            "observations_learned": stats.observation_count,
            "reason": " | ".join(reasons) if reasons else "Normal",
        }

    def get_model_summary(self) -> Dict:
        if self.model is None:
            return {"status": "not_initialized"}
        stats = self.model.get_stats()
        return {
            "boundary_id": self.boundary_id,
            "model_locked": self.model_locked,
            "observations_learned": stats.observation_count,
            "n_metrics": len(stats.metric_names),
            "metric_names": stats.metric_names,
            "mean": stats.mean.tolist() if stats.mean is not None else None,
            "condition_number": stats.condition_number,
            "is_singular": stats.is_singular,
            "invariants_learned": len(self.invariant_learner.invariants),
        }
