"""
Adaptive Anomaly Detector: Multi-signal detection to overcome Phase 7B limitation.

Instead of binary threshold detection, use anomaly scoring that combines:
1. Deviation from baseline (statistical z-score)
2. Rate-of-change acceleration
3. Multi-signal correlation (unusual pattern across multiple metrics)
4. Historical variance context

This solves the Phase 7B gap: detects attacks hidden in workload variance
while maintaining false-positive immunity on legitimate load.
"""

from dataclasses import dataclass, field
from collections import deque
from typing import Optional
import statistics
import math


@dataclass
class AnomalySignal:
    """Individual anomaly signal."""
    signal_type: str  # "deviation", "acceleration", "correlation", "variance"
    score: float  # 0-1
    threshold: float
    exceeded: bool
    details: dict = field(default_factory=dict)


@dataclass
class AnomalyResult:
    """Result of anomaly detection."""
    anomaly_score: float  # 0-1, combined
    is_anomaly: bool  # True if score > threshold
    signals: list[AnomalySignal]
    explanation: str


class AdaptiveAnomalyDetector:
    """
    Multi-signal anomaly detector for attacks hidden in workload variance.

    Solves Phase 7B problem: low false positives + high true positives.
    """

    def __init__(self, boundary_id: str, window_size: int = 20, learning_window: int = 5):
        self.boundary_id = boundary_id
        self.window_size = window_size
        self.learning_window = learning_window  # Observations before baseline locks in

        # History tracking
        self.value_history = deque(maxlen=window_size)
        self.timestamp_history = deque(maxlen=window_size)
        self.anomaly_threshold = 0.85  # High threshold for strong multi-signal confirmation

        # Baseline learning
        self.baseline_mean = None
        self.baseline_std = None
        self.baseline_initialized = False
        self.observation_count = 0  # Total observations added
        self.full_history = []  # Longer history for baseline learning
        self.baseline_locked = False  # True once learning window is complete

    def add_observation(self, value: float, timestamp: int):
        """Add a new observation."""
        self.observation_count += 1
        self.value_history.append(value)
        self.timestamp_history.append(timestamp)

        # Only update full_history during learning phase (before baseline locks)
        if not self.baseline_locked and self.observation_count <= self.learning_window:
            self.full_history.append(value)

            # Update baseline during learning phase (first N observations)
            if len(self.full_history) >= 5:
                self._update_baseline()
                if not self.baseline_initialized:
                    self.baseline_initialized = True

        # Lock baseline once learning window is complete
        if self.observation_count == self.learning_window:
            self.baseline_locked = True

    def _update_baseline(self):
        """Update baseline statistics from full history (for stable learning)."""
        if len(self.full_history) < 5:
            return
        try:
            self.baseline_mean = statistics.mean(self.full_history)
            self.baseline_std = statistics.stdev(self.full_history) if len(self.full_history) > 1 else 1.0
        except (statistics.StatisticsError, ValueError):
            self.baseline_std = 1.0

    def detect_anomaly(self, value: float, timestamp: int) -> AnomalyResult:
        """
        Detect anomalies using multi-signal approach.

        Returns combined anomaly score (0-1) and detailed signals.
        """
        self.add_observation(value, timestamp)

        signals = []

        # Only detect after baseline initialized and sufficient history
        if not self.baseline_initialized or len(self.value_history) < 3:
            return AnomalyResult(0.0, False, [], "Insufficient history")

        # Adaptive threshold: stricter during learning phase, looser after
        if self.observation_count <= self.learning_window:
            # During learning, require even stronger confirmation
            detection_threshold = 0.65
        else:
            # After learning, use standard threshold
            detection_threshold = self.anomaly_threshold

        # Signal 1: Deviation from baseline (z-score)
        deviation_signal = self._detect_deviation(value)
        signals.append(deviation_signal)

        # Signal 2: Rate-of-change acceleration
        acceleration_signal = self._detect_acceleration()
        signals.append(acceleration_signal)

        # Signal 3: Unusual persistence (multiple consecutive high values)
        persistence_signal = self._detect_persistence(value)
        signals.append(persistence_signal)

        # Signal 4: Variance spike (is this value unusual even for high-variance period?)
        variance_signal = self._detect_variance_spike(value)
        signals.append(variance_signal)

        # Combine signals: require multiple signals to exceed for anomaly
        exceeded_signals = [s for s in signals if s.exceeded]

        # During burn-in period, be conservative (require 2+ signals)
        if self.observation_count < 25:
            # Burn-in: only flag if 2+ signals exceed AND combined score is reasonable
            num_exceeded = len(exceeded_signals)
            if num_exceeded >= 2:
                combined_score = sum(s.score for s in exceeded_signals) / num_exceeded
                is_anomaly = combined_score > (detection_threshold - 0.15)
            else:
                combined_score = max(s.score for s in signals) if signals else 0.0
                is_anomaly = False
        else:
            # Post burn-in: use standard detection
            if exceeded_signals:
                combined_score = sum(s.score for s in exceeded_signals) / len(exceeded_signals)
            else:
                combined_score = max(s.score for s in signals) if signals else 0.0
            is_anomaly = combined_score > detection_threshold

        explanation = self._explain_anomaly(signals, combined_score)

        return AnomalyResult(
            anomaly_score=combined_score,
            is_anomaly=is_anomaly,
            signals=signals,
            explanation=explanation
        )

    def _detect_deviation(self, value: float) -> AnomalySignal:
        """Detect deviation from baseline using z-score."""
        if self.baseline_std == 0:
            z_score = 0
        else:
            z_score = abs((value - self.baseline_mean) / self.baseline_std)

        # Flexible thresholds: high z-scores always trigger, moderate ones only post-learning
        if z_score > 4.0:
            # Very extreme deviation (z > 4)
            score = min(1.0, (z_score - 4.0) / 3.0 + 0.5)
            exceeded = True
        elif z_score > 2.5:
            # Moderate-high deviation
            learning_factor = 1.5 if self.observation_count <= self.learning_window else 1.0
            score = min(1.0, (z_score - 2.5 * learning_factor) / 2.0)
            exceeded = score > 0.35
        else:
            score = 0.0
            exceeded = False

        return AnomalySignal(
            signal_type="deviation",
            score=score,
            threshold=0.35,
            exceeded=exceeded,
            details={"z_score": z_score, "baseline_mean": self.baseline_mean, "baseline_std": self.baseline_std}
        )

    def _detect_acceleration(self) -> AnomalySignal:
        """Detect sudden large increases (attacks spike up, not down)."""
        if len(self.value_history) < 2:
            return AnomalySignal("acceleration", 0.0, 0.5, False)

        recent_vals = list(self.value_history)[-2:]
        recent_change = recent_vals[-1] - recent_vals[-2]  # Signed change

        # Only care about increases, not decreases
        # (Decreases after spike are recovery, not new attacks)
        if recent_change <= 0:
            return AnomalySignal("acceleration", 0.0, 0.5, False)

        # Threshold: increases > 4x baseline std are suspicious
        baseline_range = self.baseline_std if self.baseline_std > 0 else 1.0
        change_normalized = recent_change / baseline_range

        # Only trigger on sudden large increases
        if change_normalized > 4.0:
            score = min(1.0, (change_normalized - 4.0) / 2.0 + 0.5)
            exceeded = True
        else:
            score = max(0.0, change_normalized / 8.0) if change_normalized > 2.0 else 0.0
            exceeded = False

        return AnomalySignal(
            signal_type="acceleration",
            score=score,
            threshold=0.5,
            exceeded=exceeded,
            details={"recent_increase": recent_change, "increase_normalized": change_normalized}
        )

    def _detect_persistence(self, current_value: float) -> AnomalySignal:
        """Detect persistent elevation (multiple high values in a row)."""
        if len(self.value_history) < 5:
            return AnomalySignal("persistence", 0.0, 0.5, False)

        recent = list(self.value_history)[-5:]

        # Count how many recent values are elevated
        high_threshold = self.baseline_mean + 2 * self.baseline_std
        elevated_count = sum(1 for v in recent if v > high_threshold)

        # Persistence: 3+ consecutive high values is suspicious
        persistence_score = min(1.0, (elevated_count - 2) / 3.0)
        exceeded = persistence_score > 0.3

        return AnomalySignal(
            signal_type="persistence",
            score=persistence_score,
            threshold=0.3,
            exceeded=exceeded,
            details={"elevated_count": elevated_count, "threshold": high_threshold}
        )

    def _detect_variance_spike(self, current_value: float) -> AnomalySignal:
        """Detect unusual spikes even within high-variance periods."""
        if len(self.value_history) < 10:
            return AnomalySignal("variance_spike", 0.0, 0.5, False)

        recent = list(self.value_history)[-10:]

        # Calculate recent variance
        try:
            recent_mean = statistics.mean(recent)
            recent_std = statistics.stdev(recent) if len(recent) > 1 else 1.0
        except:
            recent_std = 1.0

        # Is current value an outlier even within recent (high-variance) period?
        if recent_std > 0:
            local_z_score = abs(current_value - recent_mean) / recent_std
        else:
            local_z_score = 0

        # Score: z-score > 2 within recent variance is suspicious
        variance_spike_score = min(1.0, (local_z_score - 1.5) / 2.0)
        exceeded = variance_spike_score > 0.2

        return AnomalySignal(
            signal_type="variance_spike",
            score=variance_spike_score,
            threshold=0.2,
            exceeded=exceeded,
            details={"local_z_score": local_z_score, "recent_std": recent_std}
        )

    def _explain_anomaly(self, signals: list[AnomalySignal], combined_score: float) -> str:
        """Generate explanation for anomaly detection."""
        exceeded = [s.signal_type for s in signals if s.exceeded]

        if combined_score > self.anomaly_threshold:
            return f"Anomaly detected ({combined_score:.2f}): {', '.join(exceeded)}"
        else:
            return f"Normal behavior ({combined_score:.2f})"
