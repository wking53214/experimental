"""
Adaptive Anomaly Detector: Multi-signal detection to overcome Phase 7B limitation.

Instead of binary threshold detection, use anomaly scoring that combines:
1. Deviation from baseline (statistical z-score)
2. Rate-of-change acceleration
3. Multi-signal correlation (unusual pattern across multiple metrics)
4. Historical variance context

This solves the Phase 7B gap: detects attacks hidden in workload variance
while maintaining false-positive immunity on legitimate load.

Phase 9 hardening: regime-aware EMA baselines, self-poisoning fix,
cyclic FP reduction.
"""

from dataclasses import dataclass, field
from collections import deque
from typing import Optional
import statistics
import math


@dataclass
class AnomalySignal:
    """Individual anomaly signal."""
    signal_type: str
    score: float
    threshold: float
    exceeded: bool
    details: dict = field(default_factory=dict)


@dataclass
class AnomalyResult:
    """Result of anomaly detection."""
    anomaly_score: float
    is_anomaly: bool
    signals: list
    explanation: str


class AdaptiveAnomalyDetector:
    """Multi-signal anomaly detector with regime-aware baselines."""

    def __init__(self, boundary_id: str, window_size: int = 48, learning_window: int = 12):
        self.boundary_id = boundary_id
        self.window_size = window_size
        self.learning_window = learning_window
        self.value_history = deque(maxlen=window_size)
        self.timestamp_history = deque(maxlen=window_size)
        self.anomaly_threshold = 0.75
        self.baseline_mean = None
        self.baseline_std = None
        self.baseline_initialized = False
        self.observation_count = 0
        self.full_history = deque(maxlen=max(window_size * 2, 96))
        self.baseline_locked = False
        self._adaptation_rate = 0.12

    def add_observation(self, value: float, timestamp: int):
        self.observation_count += 1
        self.value_history.append(value)
        self.timestamp_history.append(timestamp)
        self.full_history.append(value)
        if len(self.full_history) >= 5:
            self._update_baseline()
            if not self.baseline_initialized:
                self.baseline_initialized = True
        if self.observation_count >= self.learning_window:
            self.baseline_locked = True

    def _update_baseline(self):
        if len(self.full_history) < 5:
            return
        try:
            recent = list(self.full_history)
            new_mean = statistics.mean(recent)
            new_std = statistics.stdev(recent) if len(recent) > 1 else 1.0
            if self.baseline_mean is None or not self.baseline_locked:
                self.baseline_mean = new_mean
                self.baseline_std = max(new_std, 1.0)
            else:
                alpha = self._adaptation_rate
                self.baseline_mean = (1 - alpha) * self.baseline_mean + alpha * new_mean
                self.baseline_std = max((1 - alpha) * self.baseline_std + alpha * new_std, 1.0)
        except (statistics.StatisticsError, ValueError):
            if self.baseline_std is None:
                self.baseline_std = 1.0

    def detect_anomaly(self, value: float, timestamp: int) -> AnomalyResult:
        """Detect anomalies; signals computed BEFORE observation is added (anti self-poisoning)."""
        signals = []
        if not self.baseline_initialized or len(self.value_history) < 3:
            self.add_observation(value, timestamp)
            return AnomalyResult(0.0, False, [], "Insufficient history")

        detection_threshold = self.anomaly_threshold
        signals.append(self._detect_deviation(value))
        prev = self.value_history[-1] if self.value_history else value
        signals.append(self._detect_acceleration_with_change(value - prev))
        signals.append(self._detect_persistence(value))
        signals.append(self._detect_variance_spike(value))
        self.add_observation(value, timestamp)

        exceeded_signals = [s for s in signals if s.exceeded]
        num_exceeded = len(exceeded_signals)
        if exceeded_signals:
            combined_score = sum(s.score for s in exceeded_signals) / num_exceeded
        else:
            combined_score = max((s.score for s in signals), default=0.0)

        strong_single = any(s.score >= 0.75 for s in signals)
        if strong_single and self.baseline_locked and combined_score >= 0.60:
            is_anomaly = True
        elif num_exceeded >= 2 and combined_score > detection_threshold:
            is_anomaly = True
        else:
            is_anomaly = False

        return AnomalyResult(combined_score, is_anomaly, signals, self._explain_anomaly(signals, combined_score))

    def _detect_deviation(self, value: float) -> AnomalySignal:
        if self.baseline_mean is None or self.baseline_std is None or self.baseline_std == 0:
            z_score = 0.0
        else:
            z_score = abs((value - self.baseline_mean) / self.baseline_std)
        if z_score > 4.0:
            score = min(1.0, (z_score - 4.0) / 2.5 + 0.7)
            exceeded = True
        elif z_score > 2.8:
            score = min(1.0, (z_score - 2.8) / 2.0 + 0.3)
            exceeded = score > 0.35
        elif z_score > 2.0:
            score = min(0.5, (z_score - 2.0) / 2.0)
            exceeded = False
        else:
            score = 0.0
            exceeded = False
        return AnomalySignal("deviation", score, 0.35, exceeded, {"z_score": z_score, "baseline_mean": self.baseline_mean, "baseline_std": self.baseline_std})

    def _detect_acceleration(self) -> AnomalySignal:
        if len(self.value_history) < 2:
            return AnomalySignal("acceleration", 0.0, 0.5, False)
        recent_vals = list(self.value_history)[-2:]
        return self._detect_acceleration_with_change(recent_vals[-1] - recent_vals[-2])

    def _detect_acceleration_with_change(self, recent_change: float) -> AnomalySignal:
        if recent_change <= 0:
            return AnomalySignal("acceleration", 0.0, 0.5, False)
        baseline_range = self.baseline_std if (self.baseline_std and self.baseline_std > 0) else 1.0
        change_normalized = recent_change / baseline_range
        if change_normalized > 5.0:
            score = min(1.0, (change_normalized - 5.0) / 2.0 + 0.6)
            exceeded = True
        elif change_normalized > 3.5:
            score = min(0.7, (change_normalized - 3.5) / 3.0 + 0.2)
            exceeded = score > 0.45
        else:
            score = 0.0
            exceeded = False
        return AnomalySignal("acceleration", score, 0.5, exceeded, {"recent_increase": recent_change, "increase_normalized": change_normalized})

    def _detect_persistence(self, current_value: float) -> AnomalySignal:
        if len(self.value_history) < 5 or self.baseline_mean is None:
            return AnomalySignal("persistence", 0.0, 0.5, False)
        recent = list(self.value_history)[-5:]
        multiplier = 2.5 if self.baseline_locked else 2.0
        high_threshold = self.baseline_mean + multiplier * (self.baseline_std or 1.0)
        elevated_count = sum(1 for v in recent if v > high_threshold)
        if self.baseline_locked:
            persistence_score = min(1.0, max(0.0, (elevated_count - 3) / 2.0))
            exceeded = elevated_count >= 4 and persistence_score > 0.4
        else:
            persistence_score = min(1.0, max(0.0, (elevated_count - 2) / 3.0))
            exceeded = persistence_score > 0.3
        return AnomalySignal("persistence", persistence_score, 0.3, exceeded, {"elevated_count": elevated_count, "threshold": high_threshold})

    def _detect_variance_spike(self, current_value: float) -> AnomalySignal:
        if len(self.value_history) < 10 or self.baseline_mean is None:
            return AnomalySignal("variance_spike", 0.0, 0.5, False)
        recent = list(self.value_history)[-10:]
        try:
            recent_mean = statistics.mean(recent)
            recent_std = statistics.stdev(recent) if len(recent) > 1 else 1.0
        except Exception:
            recent_std = 1.0
        local_z_score = abs(current_value - recent_mean) / recent_std if recent_std > 0 else 0.0
        global_z = abs(current_value - self.baseline_mean) / self.baseline_std if (self.baseline_std and self.baseline_std > 0) else 0.0
        if local_z_score > 3.0 and global_z > 3.0:
            variance_spike_score = min(1.0, (local_z_score - 3.0) / 2.0 + 0.5)
            exceeded = True
        else:
            variance_spike_score = 0.0
            exceeded = False
        return AnomalySignal("variance_spike", variance_spike_score, 0.5, exceeded, {"local_z_score": local_z_score, "global_z": global_z, "recent_std": recent_std})

    def _explain_anomaly(self, signals, combined_score: float) -> str:
        exceeded = [s.signal_type for s in signals if s.exceeded]
        if combined_score > self.anomaly_threshold:
            return f"Anomaly detected ({combined_score:.2f}): {', '.join(exceeded)}"
        return f"Normal behavior ({combined_score:.2f})"
