"""
Phase 9D: Online Learning with Concept Drift

Handles the challenge of legitimate system behavior changes without
flagging them as attacks. Implements adaptive baseline that tracks
gradual shifts in system operation while remaining sensitive to attacks.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import deque


class ConceptDriftDetector:
    """
    Detects when system behavior has fundamentally changed.

    Distinguishes:
    - Gradual drift: Legitimate system evolution (accommodate)
    - Sudden shift: Potential attack or critical change (flag)
    """

    def __init__(self, window_size: int = 100, drift_threshold: float = 0.3):
        """
        Initialize drift detector.

        Args:
            window_size: Size of sliding window for computing drift
            drift_threshold: KL-divergence threshold for drift detection
        """
        self.window_size = window_size
        self.drift_threshold = drift_threshold

        self.historical_dist: Optional[Tuple[np.ndarray, np.ndarray]] = None  # (mean, std)
        self.recent_observations = deque(maxlen=window_size)
        self.drift_history: List[float] = []

    def update(self, observation: Dict[str, float]) -> None:
        """Update drift detector with new observation."""
        if not observation:
            return

        values = np.array(list(observation.values()))
        self.recent_observations.append(values)

        # Recompute drift estimate
        if len(self.recent_observations) >= 10:
            recent_mean = np.mean([obs for obs in self.recent_observations], axis=0)
            recent_std = np.std([obs for obs in self.recent_observations], axis=0)

            if self.historical_dist is not None:
                hist_mean, hist_std = self.historical_dist
                drift = self._compute_kl_divergence(hist_mean, hist_std, recent_mean, recent_std)
                self.drift_history.append(drift)
            else:
                self.historical_dist = (recent_mean, recent_std)

    def _compute_kl_divergence(self, mean1: np.ndarray, std1: np.ndarray,
                               mean2: np.ndarray, std2: np.ndarray) -> float:
        """
        Compute approximate KL divergence between two Gaussians.

        KL(P||Q) ≈ 0.5 * sum(log(σ2²/σ1²) + (σ1² + (μ1-μ2)²)/σ2² - 1)
        """
        std1 = np.maximum(std1, 1e-6)
        std2 = np.maximum(std2, 1e-6)

        kl = 0.5 * np.sum(
            np.log(std2**2 / std1**2) +
            (std1**2 + (mean1 - mean2)**2) / std2**2 - 1
        )

        return float(np.clip(kl, 0.0, 10.0))

    def get_drift_status(self) -> Dict:
        """Get current concept drift status."""
        if not self.drift_history:
            return {
                "has_drift": False,
                "drift_score": 0.0,
                "drift_type": "none",
            }

        recent_drift = np.mean(self.drift_history[-10:]) if len(self.drift_history) >= 10 else self.drift_history[-1]

        return {
            "has_drift": recent_drift > self.drift_threshold,
            "drift_score": recent_drift,
            "drift_type": "sudden" if len(self.drift_history) > 0 and self.drift_history[-1] > 0.7 else "gradual",
            "drift_history": self.drift_history[-20:],
        }


class OnlineAdaptiveBaseline:
    """
    Baseline that adapts to legitimate concept drift without accommodating attacks.

    Uses weighted updates: old observations decay, recent observations count more.
    Detects and rejects anomalous observations before updating.
    """

    def __init__(self, decay_factor: float = 0.95):
        """
        Initialize adaptive baseline.

        Args:
            decay_factor: How much to weight recent vs old observations (0.95 = recent weighted 2x more)
        """
        self.decay_factor = decay_factor
        self.weighted_mean: Optional[np.ndarray] = None
        self.weighted_variance: Optional[np.ndarray] = None
        self.observation_count = 0
        self.rejected_count = 0

    def update_with_outlier_rejection(self, observation: Dict[str, float],
                                     mahalanobis_distance: float,
                                     detection_threshold: float = 3.0) -> Tuple[bool, str]:
        """
        Update baseline, rejecting observations that look anomalous.

        Args:
            observation: New metric values
            mahalanobis_distance: Mahalanobis distance from current baseline
            detection_threshold: Threshold for anomaly (typically 3σ)

        Returns:
            (accepted, reason) tuple
        """
        if mahalanobis_distance > detection_threshold:
            # Potential attack; don't learn from it
            self.rejected_count += 1
            return False, f"Rejected: MD={mahalanobis_distance:.2f} > threshold={detection_threshold}"

        # Accept observation and update baseline
        values = np.array(list(observation.values()))

        if self.weighted_mean is None:
            self.weighted_mean = values.copy()
            self.weighted_variance = np.zeros_like(values)
        else:
            # Exponential moving average
            delta = values - self.weighted_mean
            self.weighted_mean = self.decay_factor * self.weighted_mean + (1 - self.decay_factor) * values

            # Update variance
            self.weighted_variance = (
                self.decay_factor * self.weighted_variance +
                (1 - self.decay_factor) * (delta ** 2)
            )

        self.observation_count += 1
        return True, "Accepted and baseline updated"

    def get_current_baseline(self) -> Dict:
        """Get current learned baseline."""
        if self.weighted_mean is None:
            return {"status": "not_initialized"}

        return {
            "mean": self.weighted_mean.tolist() if self.weighted_mean is not None else None,
            "std": np.sqrt(np.maximum(self.weighted_variance, 0)).tolist() if self.weighted_variance is not None else None,
            "observation_count": self.observation_count,
            "rejected_count": self.rejected_count,
        }


class AdaptiveDetector:
    """
    Combines concept drift detection with online learning.

    Adapts to system changes while rejecting attacks.
    """

    def __init__(self, boundary_id: str):
        """Initialize adaptive detector."""
        self.boundary_id = boundary_id
        self.drift_detector = ConceptDriftDetector()
        self.adaptive_baseline = OnlineAdaptiveBaseline()
        self.adaptation_history: List[Dict] = []

    def process_observation(self, observation: Dict[str, float],
                           mahalanobis_distance: float) -> Dict:
        """
        Process observation with adaptation.

        Returns:
            Dict with adaptation and detection info
        """
        # Update drift detector
        self.drift_detector.update(observation)
        drift_status = self.drift_detector.get_drift_status()

        # Update adaptive baseline (may reject anomalies)
        accepted, reason = self.adaptive_baseline.update_with_outlier_rejection(
            observation,
            mahalanobis_distance
        )

        record = {
            "observation": observation,
            "accepted": accepted,
            "reason": reason,
            "drift_status": drift_status,
            "baseline": self.adaptive_baseline.get_current_baseline(),
        }

        self.adaptation_history.append(record)

        return record

    def get_adaptation_summary(self) -> Dict:
        """Get summary of adaptation process."""
        total = len(self.adaptation_history)
        accepted = sum(1 for r in self.adaptation_history if r["accepted"])

        return {
            "boundary_id": self.boundary_id,
            "total_observations": total,
            "accepted": accepted,
            "rejected": total - accepted,
            "acceptance_rate": accepted / total if total > 0 else 0.0,
            "current_drift": self.drift_detector.get_drift_status(),
            "current_baseline": self.adaptive_baseline.get_current_baseline(),
        }
