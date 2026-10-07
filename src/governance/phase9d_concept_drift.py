"""
Phase 9D: Online Learning with Concept Drift

Handles the challenge of legitimate system behavior changes without
flagging them as attacks. Implements adaptive baseline that tracks
gradual shifts in system operation while remaining sensitive to attacks.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import deque

from src.governance.multivariate import calibrated_mahalanobis_threshold


class ConceptDriftDetector:
    """
    Detects when system behavior has fundamentally changed.

    Distinguishes:
    - Gradual drift: Legitimate system evolution. The reference distribution is
      re-anchored to the current window once drift persists, so the detector keeps
      tracking the system instead of reporting drift forever.
    - Sudden shift: A large change within the window. Flagged and held (not
      re-anchored) until acknowledge() is called, so a person validates it.
    """

    def __init__(self, window_size: int = 100, drift_threshold: float = 0.3,
                 sudden_threshold: float = 0.7):
        """
        Args:
            window_size: Size of sliding window for computing drift
            drift_threshold: KL-divergence from the reference that counts as drift
            sudden_threshold: KL-divergence between the older and newer half of the
                window that counts as a sudden shift (measures speed, not size)
        """
        self.window_size = window_size
        self.drift_threshold = drift_threshold
        self.sudden_threshold = sudden_threshold

        self.historical_dist: Optional[Tuple[np.ndarray, np.ndarray]] = None  # (mean, std)
        self.recent_observations = deque(maxlen=window_size)
        self.drift_history: List[float] = []
        self.sudden_flagged = False
        self.reanchor_count = 0

    def update(self, observation: Dict[str, float]) -> None:
        """Update drift detector with new observation."""
        if not observation:
            return

        values = np.array(list(observation.values()))
        self.recent_observations.append(values)
        n = len(self.recent_observations)

        # Only compare half-windows once the window is full: with few observations the
        # estimates are noisy enough to trigger a false (and sticky) sudden-shift flag.
        if n == self.window_size and n >= 40:
            arr = np.array(list(self.recent_observations))
            half = n // 2
            change = self._compute_kl_divergence(
                arr[:half].mean(axis=0), arr[:half].std(axis=0),
                arr[half:].mean(axis=0), arr[half:].std(axis=0))
            if change > self.sudden_threshold:
                self.sudden_flagged = True

        if n >= 10:
            recent_mean = np.mean([obs for obs in self.recent_observations], axis=0)
            recent_std = np.std([obs for obs in self.recent_observations], axis=0)

            if self.historical_dist is not None:
                hist_mean, hist_std = self.historical_dist
                drift = self._compute_kl_divergence(hist_mean, hist_std, recent_mean, recent_std)
                self.drift_history.append(drift)
                if (n == self.window_size and not self.sudden_flagged
                        and self._recent_drift() > self.drift_threshold):
                    self._reanchor(recent_mean, recent_std)
            else:
                self.historical_dist = (recent_mean, recent_std)

    def _recent_drift(self) -> float:
        return float(np.mean(self.drift_history[-10:]))

    def _reanchor(self, mean: np.ndarray, std: np.ndarray) -> None:
        self.historical_dist = (mean, std)
        self.drift_history.clear()
        self.reanchor_count += 1

    def acknowledge(self) -> None:
        """Operator has validated a flagged shift: accept the current window as normal."""
        if self.recent_observations:
            arr = np.array(list(self.recent_observations))
            self._reanchor(arr.mean(axis=0), arr.std(axis=0))
        self.sudden_flagged = False

    def _compute_kl_divergence(self, mean1: np.ndarray, std1: np.ndarray,
                               mean2: np.ndarray, std2: np.ndarray) -> float:
        """
        Approximate KL divergence between two Gaussians, per metric; returns the
        largest per-metric value.

        KL(P||Q) = 0.5 * (log(σ2²/σ1²) + (σ1² + (μ1-μ2)²)/σ2² - 1)

        The maximum (not the sum) keeps the thresholds independent of how many metrics
        are tracked, while a shift in any single metric is still caught.
        """
        std1 = np.maximum(std1, 1e-6)
        std2 = np.maximum(std2, 1e-6)

        kl = 0.5 * (
            np.log(std2**2 / std1**2) +
            (std1**2 + (mean1 - mean2)**2) / std2**2 - 1
        )

        return float(np.clip(np.max(kl), 0.0, 10.0))

    def get_drift_status(self) -> Dict:
        """Get current concept drift status."""
        if not self.drift_history:
            return {
                "has_drift": bool(self.sudden_flagged),
                "drift_score": 0.0,
                "drift_type": "sudden" if self.sudden_flagged else "none",
                "reanchor_count": self.reanchor_count,
            }

        recent_drift = self._recent_drift()
        has_drift = bool(recent_drift > self.drift_threshold or self.sudden_flagged)

        return {
            "has_drift": has_drift,
            "drift_score": float(recent_drift),
            "drift_type": "sudden" if self.sudden_flagged else ("gradual" if has_drift else "none"),
            "drift_history": self.drift_history[-20:],
            "reanchor_count": self.reanchor_count,
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
                                     detection_threshold: Optional[float] = None) -> Tuple[bool, str]:
        """
        Update baseline, rejecting observations that look anomalous.

        Args:
            observation: New metric values
            mahalanobis_distance: Mahalanobis distance from current baseline
            detection_threshold: Rejection cutoff. None calibrates it from the number
                of metrics (a fixed 3.0 is a one-dimensional rule that rejects too much
                normal data when several metrics are tracked)

        Returns:
            (accepted, reason) tuple
        """
        if detection_threshold is None:
            detection_threshold = calibrated_mahalanobis_threshold(len(observation))
        if mahalanobis_distance > detection_threshold:
            # Potential attack; don't learn from it
            self.rejected_count += 1
            return False, f"Rejected: MD={mahalanobis_distance:.2f} > threshold={detection_threshold:.2f}"

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
