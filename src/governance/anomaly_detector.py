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

    def __init__(self, boundary_id: str, window_size: int = 20):
        self.boundary_id = boundary_id
        self.window_size = window_size

        # History tracking
        self.value_history = deque(maxlen=window_size)
        self.timestamp_history = deque(maxlen=window_size)
        self.anomaly_threshold = 0.50  # Combined score threshold (tuned for Phase 7B)

        # Baseline learning
        self.baseline_mean = None
        self.baseline_std = None
        self.baseline_initialized = False

    def add_observation(self, value: float, timestamp: int):
        """Add a new observation."""
        self.value_history.append(value)
        self.timestamp_history.append(timestamp)

        # Learn baseline from first 20 observations
        if not self.baseline_initialized and len(self.value_history) >= 10:
            try:
                self.baseline_mean = statistics.mean(self.value_history)
                self.baseline_std = statistics.stdev(self.value_history) if len(self.value_history) > 1 else 1.0
                self.baseline_initialized = True
            except (statistics.StatisticsError, ValueError):
                self.baseline_std = 1.0

    def detect_anomaly(self, value: float, timestamp: int) -> AnomalyResult:
        """
        Detect anomalies using multi-signal approach.

        Returns combined anomaly score (0-1) and detailed signals.
        """
        self.add_observation(value, timestamp)

        signals = []

        # Only detect after baseline initialized
        if not self.baseline_initialized or len(self.value_history) < 3:
            return AnomalyResult(0.0, False, [], "Insufficient history")

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

        # Combine signals: weighted average of exceeded signals
        exceeded_signals = [s for s in signals if s.exceeded]
        if exceeded_signals:
            combined_score = sum(s.score for s in exceeded_signals) / len(signals)
        else:
            combined_score = min(s.score for s in signals)

        is_anomaly = combined_score > self.anomaly_threshold

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

        # Z-score > 2 is unusual, > 3 is very unusual
        score = min(1.0, z_score / 3.0)
        exceeded = score > 0.5

        return AnomalySignal(
            signal_type="deviation",
            score=score,
            threshold=0.5,
            exceeded=exceeded,
            details={"z_score": z_score, "baseline_mean": self.baseline_mean, "baseline_std": self.baseline_std}
        )

    def _detect_acceleration(self) -> AnomalySignal:
        """Detect rate-of-change acceleration (second derivative)."""
        if len(self.value_history) < 3:
            return AnomalySignal("acceleration", 0.0, 0.5, False)

        recent = list(self.value_history)[-3:]

        # Calculate velocity (first derivative)
        v1 = recent[1] - recent[0]
        v2 = recent[2] - recent[1]

        # Calculate acceleration (second derivative)
        acceleration = v2 - v1

        # Large acceleration indicates sudden change
        baseline_range = self.baseline_std if self.baseline_std > 0 else 1.0
        acceleration_normalized = abs(acceleration) / baseline_range

        score = min(1.0, acceleration_normalized / 2.0)
        exceeded = score > 0.5

        return AnomalySignal(
            signal_type="acceleration",
            score=score,
            threshold=0.5,
            exceeded=exceeded,
            details={"acceleration": acceleration, "velocity_change": v2 - v1}
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
