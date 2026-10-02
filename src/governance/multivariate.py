"""
Phase 9A: Generative Anomaly Detection via Multivariate Gaussian Learning

Learns the full covariance structure of normal system behavior and scores
observations using Mahalanobis distance. Catches novel attacks that distort
the multivariate distribution, even if individual metrics remain normal.

Core insight: An attack is statistically anomalous relative to normal
operation's learned distribution, regardless of attack type/signature.
"""

import numpy as np
import time
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field


@dataclass
class GaussianStats:
    """Sufficient statistics for multivariate Gaussian."""
    metric_names: List[str]
    mean: np.ndarray
    cov_matrix: np.ndarray
    observation_count: int
    min_eigenvalue: float = field(default=0.0)
    condition_number: float = field(default=0.0)
    is_singular: bool = field(default=False)


class MultivariateGaussian:
    """
    Learn and maintain a multivariate Gaussian model of system behavior.

    Captures not just individual metric distributions, but their joint
    covariance structure - how they move together under normal operation.
    """

    def __init__(self, metric_names: List[str], regularization: float = 1e-6):
        """
        Initialize multivariate model.

        Args:
            metric_names: Ordered list of metric names
            regularization: Add λI to covariance for numerical stability
        """
        self.metric_names = metric_names
        self.n_metrics = len(metric_names)
        self.regularization = regularization

        # Sufficient statistics
        self.n = 0
        self.mean = np.zeros(self.n_metrics)
        self.M2 = np.zeros((self.n_metrics, self.n_metrics))  # Second moment for Welford's algorithm

        # Cached results
        self._cov_matrix = None
        self._cov_inverse = None
        self._cov_determinant = None
        self._is_valid = False

    def update(self, observation: Dict[str, float]) -> None:
        """
        Incrementally update statistics with one observation (Welford's algorithm).

        Args:
            observation: Dict mapping metric_name → value
        """
        # Extract in consistent order
        x = np.array([observation.get(name, 0.0) for name in self.metric_names])

        self.n += 1

        # Welford's online mean
        delta = x - self.mean
        self.mean += delta / self.n

        # Welford's online covariance
        delta2 = x - self.mean
        self.M2 += np.outer(delta, delta2)

        # Invalidate cache
        self._is_valid = False

    def get_covariance(self) -> np.ndarray:
        """Get current covariance matrix with regularization."""
        if self._cov_matrix is None or not self._is_valid:
            if self.n < 2:
                # Not enough data; return identity (maximum uncertainty)
                self._cov_matrix = np.eye(self.n_metrics)
            else:
                # Biased covariance (divide by n, not n-1, for streaming stability)
                raw_cov = self.M2 / max(1, self.n - 1)

                # Add regularization for numerical stability
                self._cov_matrix = raw_cov + self.regularization * np.eye(self.n_metrics)

        return self._cov_matrix

    def get_cov_inverse(self) -> Optional[np.ndarray]:
        """Get inverse of covariance matrix (for Mahalanobis distance)."""
        if self._cov_inverse is None or not self._is_valid:
            cov = self.get_covariance()
            try:
                self._cov_inverse = np.linalg.inv(cov)
            except np.linalg.LinAlgError:
                # Singular matrix; use pseudoinverse
                self._cov_inverse = np.linalg.pinv(cov)

        return self._cov_inverse

    def get_determinant(self) -> float:
        """Get determinant of covariance (for log-likelihood)."""
        if self._cov_determinant is None or not self._is_valid:
            cov = self.get_covariance()
            self._cov_determinant = np.linalg.det(cov)
            # Clamp to avoid log of zero
            self._cov_determinant = max(1e-10, self._cov_determinant)

        return self._cov_determinant

    def get_stats(self) -> GaussianStats:
        """Get current model statistics."""
        cov = self.get_covariance()

        # Condition number (stability metric)
        try:
            eigenvalues = np.linalg.eigvalsh(cov)
            min_eig = np.min(eigenvalues)
            max_eig = np.max(eigenvalues)
            condition = max_eig / max(min_eig, 1e-10)
        except:
            min_eig = 0.0
            condition = np.inf

        return GaussianStats(
            metric_names=self.metric_names,
            mean=self.mean.copy(),
            cov_matrix=cov.copy(),
            observation_count=self.n,
            min_eigenvalue=min_eig,
            condition_number=condition,
            is_singular=min_eig < 1e-10
        )

    def mahalanobis_distance(self, observation: Dict[str, float]) -> float:
        """
        Compute Mahalanobis distance of observation from learned mean.

        Lower distance = more consistent with normal operation.
        Distance > 3 is typically anomalous (3σ in multivariate space).

        Returns:
            Mahalanobis distance (>= 0)
        """
        x = np.array([observation.get(name, 0.0) for name in self.metric_names])
        delta = x - self.mean

        cov_inv = self.get_cov_inverse()
        if cov_inv is None:
            return 0.0

        # d_M = sqrt((x - μ)^T Σ^(-1) (x - μ))
        try:
            md_squared = delta @ cov_inv @ delta
            md = np.sqrt(max(0.0, md_squared))
            return float(md)
        except:
            return 0.0

    def log_likelihood(self, observation: Dict[str, float]) -> float:
        """
        Compute log-likelihood of observation under learned Gaussian.

        Higher = more likely under normal operation.
        Lower = more anomalous.

        Returns:
            Log-likelihood (can be negative)
        """
        md = self.mahalanobis_distance(observation)
        det = self.get_determinant()

        # log p(x) = -0.5 * [k*log(2π) + log|Σ| + d_M^2]
        k = self.n_metrics
        ll = -0.5 * (k * np.log(2 * np.pi) + np.log(det) + md**2)
        return float(ll)


class MahalanobisAnomalyScorer:
    """
    Score observations using Mahalanobis distance.

    Converts distance into anomaly score (0=normal, 1=certain anomaly).
    """

    def __init__(self, threshold: float = 3.0, saturation: float = 5.0):
        """
        Initialize scorer.

        Args:
            threshold: Mahalanobis distance considered anomalous (typically 3σ)
            saturation: Distance at which score saturates to 1.0
        """
        self.threshold = threshold
        self.saturation = saturation

    def score(self, mahalanobis_distance: float) -> float:
        """
        Convert Mahalanobis distance to anomaly score.

        Returns:
            Score in [0, 1] where 0=normal, 1=certain anomaly
        """
        if mahalanobis_distance < self.threshold:
            return 0.0

        if mahalanobis_distance >= self.saturation:
            return 1.0

        # Linear interpolation in anomaly region
        score = (mahalanobis_distance - self.threshold) / (self.saturation - self.threshold)
        return float(np.clip(score, 0.0, 1.0))


class InvariantLearner:
    """
    Learn metric relationships that should be preserved under normal operation.

    An "invariant" is a relationship between metrics that holds consistently.
    Example: "if error_rate decreases, throughput should not decrease proportionally"
    """

    def __init__(self, min_observations: int = 50):
        """
        Initialize invariant learner.

        Args:
            min_observations: Minimum observations before learning invariants
        """
        self.min_observations = min_observations
        self.observations: List[Dict[str, float]] = []
        self.invariants: Dict[Tuple[str, str], Dict] = {}  # (metric1, metric2) → relationship

    def add_observation(self, observation: Dict[str, float]) -> None:
        """Add observation to history."""
        self.observations.append(observation.copy())

        # Keep only recent observations (sliding window)
        if len(self.observations) > self.min_observations * 2:
            self.observations = self.observations[-self.min_observations:]

    def learn_invariants(self) -> Dict[Tuple[str, str], Dict]:
        """
        Learn metric relationships from history.

        Returns:
            Dict of (metric1, metric2) → {correlation, direction, strength}
        """
        if len(self.observations) < self.min_observations:
            return {}

        # Get all metric names
        metric_names = list(self.observations[0].keys())

        # Compute pairwise correlations
        for m1 in metric_names:
            for m2 in metric_names:
                if m1 >= m2:  # Avoid duplicates
                    continue

                values1 = np.array([o.get(m1, 0.0) for o in self.observations])
                values2 = np.array([o.get(m2, 0.0) for o in self.observations])

                # Skip if either metric is constant
                if np.std(values1) < 1e-10 or np.std(values2) < 1e-10:
                    continue

                # Compute correlation
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
        """
        Check if observation violates any learned invariants.

        Returns:
            Dict with violation details if found, None otherwise
        """
        if not self.invariants:
            return None

        values = np.array(list(observation.values()))
        mean_val = float(np.mean(values))

        for (m1, m2), invariant in self.invariants.items():
            if m1 not in observation or m2 not in observation:
                continue

            v1 = observation[m1]
            v2 = observation[m2]

            # Compute observed correlation in local window
            # (simplified: just check sign consistency)
            expected_corr = invariant["correlation"]

            # If expected positive correlation but values move opposite, flag
            if expected_corr > 0.5:
                # Should move together
                if v1 > mean_val and v2 < mean_val:
                    return {
                        "type": "invariant_violation",
                        "metrics": (m1, m2),
                        "expected_correlation": expected_corr,
                        "severity": abs(expected_corr),
                    }

        return None


class GenerativeAnomalyDetector:
    """
    Phase 9A: Generative anomaly detection via multivariate Gaussian model.

    Learns statistical properties of normal operation and flags observations
    that are statistically unlikely, catching novel attacks that distort the
    multivariate distribution.
    """

    def __init__(self,
                 boundary_id: str,
                 min_observations: int = 20,
                 mahalanobis_threshold: float = 3.0,
                 regularization: float = 1e-6):
        """
        Initialize generative detector.

        Args:
            boundary_id: Boundary identifier
            min_observations: Observations needed before anomaly detection
            mahalanobis_threshold: Mahalanobis distance threshold (typically 3σ)
            regularization: Covariance regularization for stability
        """
        self.boundary_id = boundary_id
        self.min_observations = min_observations

        self.model: Optional[MultivariateGaussian] = None
        self.scorer = MahalanobisAnomalyScorer(threshold=mahalanobis_threshold)
        self.invariant_learner = InvariantLearner(min_observations=min_observations)

        self.observation_count = 0
        self.model_locked = False
        self.regularization = regularization

    def ingest_observation(self, observation: Dict[str, float]) -> None:
        """Ingest observation into model."""
        # Initialize model on first observation
        if self.model is None:
            self.model = MultivariateGaussian(
                list(observation.keys()),
                regularization=self.regularization
            )

        # Update model
        self.model.update(observation)
        self.invariant_learner.add_observation(observation)
        self.observation_count += 1

        # Lock model after sufficient observations
        if self.observation_count >= self.min_observations and not self.model_locked:
            self.model_locked = True
            self.invariant_learner.learn_invariants()

    def detect_anomaly(self, observation: Dict[str, float]) -> Dict:
        """
        Detect anomalies using multivariate Gaussian model.

        Returns:
            Dict with detection results
        """
        if self.model is None:
            return {
                "anomaly_detected": False,
                "anomaly_score": 0.0,
                "mahalanobis_distance": 0.0,
                "reason": "Model not yet trained",
            }

        if not self.model_locked:
            return {
                "anomaly_detected": False,
                "anomaly_score": 0.0,
                "mahalanobis_distance": 0.0,
                "reason": f"Baseline learning ({self.observation_count}/{self.min_observations})",
            }

        # Compute Mahalanobis distance
        md = self.model.mahalanobis_distance(observation)

        # Score anomaly
        anomaly_score = self.scorer.score(md)

        # Check invariants
        invariant_violation = self.invariant_learner.check_invariant_violation(observation)

        # Get model stats
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
        """Get summary of learned model."""
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
