"""
Phase 9E: Attack Precursor Learning

Learns what metric patterns precede violations, enabling earlier detection.
Instead of waiting for violation to occur, recognizes the pre-violation
signature and flags it before damage happens.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import deque


class AttackPrecursorLearner:
    """
    Learns patterns that precede violations.

    Maintains history of metric observations before violations occur,
    extracting common patterns to enable early detection.
    """

    def __init__(self, lookback_window: int = 10, min_patterns: int = 3):
        """
        Initialize precursor learner.

        Args:
            lookback_window: How many observations before violation to consider
            min_patterns: Minimum occurrences to recognize as pattern
        """
        self.lookback_window = lookback_window
        self.min_patterns = min_patterns

        self.observation_history = deque(maxlen=lookback_window * 10)
        self.violation_indices: List[int] = []  # absolute sequence numbers, not deque positions
        self._total_observed = 0
        self.learned_patterns: Dict[str, Dict] = {}
        self.pattern_detections: Dict[str, int] = {}

    def add_observation(self, observation: Dict[str, float], is_violation: bool = False) -> None:
        """Add observation to history, marking violations."""
        self.observation_history.append(observation)
        self._total_observed += 1

        if is_violation:
            self.violation_indices.append(self._total_observed - 1)

        offset = self._history_offset()
        self.violation_indices = [i for i in self.violation_indices if i >= offset]

    def _history_offset(self) -> int:
        """Absolute sequence number of the oldest observation still in history."""
        return self._total_observed - len(self.observation_history)

    def learn_precursors(self) -> Dict:
        """
        Extract common patterns before violations.

        Returns:
            Dict of learned patterns
        """
        if len(self.violation_indices) < self.min_patterns:
            return {}

        obs_list = list(self.observation_history)
        pattern_candidates: Dict[str, List] = {}

        # For each violation, look back at preceding observations
        offset = self._history_offset()
        for abs_idx in self.violation_indices[-10:]:  # Recent violations
            violation_idx = abs_idx - offset
            start = max(0, violation_idx - self.lookback_window)

            # Extract lead-time patterns (1, 2, 3, 5 steps before violation)
            for lead_time in [1, 2, 3, 5]:
                idx = violation_idx - lead_time
                if idx >= start and idx >= 0:
                    obs = obs_list[idx]

                    # Signature: which metrics deviate from norm
                    signature = self._compute_signature(obs)

                    if signature not in pattern_candidates:
                        pattern_candidates[signature] = []
                    pattern_candidates[signature].append((lead_time, idx))

        # Retain patterns that occur frequently. "normal"/"unknown" are not precursors:
        # most steps before any violation look normal, so learning them would flag
        # every ordinary observation.
        for signature, occurrences in pattern_candidates.items():
            if signature in ("normal", "unknown"):
                continue
            if len(occurrences) >= self.min_patterns:
                avg_lead_time = np.mean([lt for lt, _ in occurrences])

                self.learned_patterns[signature] = {
                    "signature": signature,
                    "lead_time_steps": float(avg_lead_time),
                    "occurrences": len(occurrences),
                    "confidence": min(1.0, len(occurrences) / len(self.violation_indices)),
                }

        return self.learned_patterns

    def _compute_signature(self, observation: Dict[str, float]) -> str:
        """
        Compute metric signature (which metrics are elevated/depressed).

        Returns:
            Signature string for pattern matching
        """
        if not self.observation_history:
            return "unknown"

        baseline = np.mean([list(o.values()) for o in self.observation_history], axis=0)
        current = np.array(list(observation.values()))

        # Deviation pattern: which metrics are 1.5σ away
        deviation = np.abs(current - baseline) / (np.std([list(o.values()) for o in self.observation_history], axis=0) + 1e-6)

        # Signature: metrics with significant deviation
        elevated = ",".join(str(i) for i, dev in enumerate(deviation) if dev > 1.5)

        return f"elevated:{elevated}" if elevated else "normal"

    def detect_precursor(self, observation: Dict[str, float]) -> Optional[Dict]:
        """
        Check if observation matches a known attack precursor.

        Returns:
            Dict with precursor match info, or None
        """
        if not self.learned_patterns:
            return None

        signature = self._compute_signature(observation)

        if signature in self.learned_patterns:
            pattern = self.learned_patterns[signature]
            self.pattern_detections[signature] = self.pattern_detections.get(signature, 0) + 1

            return {
                "precursor_detected": True,
                "signature": signature,
                "lead_time_to_violation": pattern["lead_time_steps"],
                "confidence": pattern["confidence"],
                "expected_violation_in": f"{pattern['lead_time_steps']:.0f} observations",
            }

        return None

    def get_precursor_summary(self) -> Dict:
        """Get summary of learned precursors."""
        return {
            "patterns_learned": len(self.learned_patterns),
            "violations_observed": len(self.violation_indices),
            "patterns": self.learned_patterns,
            "detections": self.pattern_detections,
            "predictive_accuracy": self._compute_predictive_accuracy(),
        }

    def _compute_predictive_accuracy(self) -> float:
        """Estimate how often detected precursors led to violations."""
        if not self.pattern_detections:
            return 0.0

        total_detections = sum(self.pattern_detections.values())

        # Rough estimate: accuracy = violations_after_precursor / total_detections
        # In production, would track this precisely
        return min(1.0, len(self.violation_indices) / max(1, total_detections))


class EarlyWarningSystem:
    """
    Combines precursor learning with real-time early warning.

    Stages:
    1. Precursor detected → 50% confidence flag
    2. Confirmed by anomaly detector → 90% confidence alert
    3. Violation occurs → Retroactively validates and updates patterns
    """

    def __init__(self, boundary_id: str):
        """Initialize early warning system."""
        self.boundary_id = boundary_id
        self.precursor_learner = AttackPrecursorLearner()
        self.warning_history: List[Dict] = []
        self.validation_history: List[Dict] = []

    def process_observation(self, observation: Dict[str, float],
                           anomaly_score: float,
                           is_violation: bool = False) -> Dict:
        """
        Process observation through early warning pipeline.

        Args:
            observation: Current metrics
            anomaly_score: Detector anomaly score (0-1)
            is_violation: Whether this observation triggered a violation

        Returns:
            Warning assessment
        """
        # Add to history
        self.precursor_learner.add_observation(observation, is_violation)

        # Check for precursor
        precursor = self.precursor_learner.detect_precursor(observation)

        warning_level = "normal"
        confidence = 0.0

        if precursor:
            # Precursor matched
            warning_level = "elevated"
            confidence = precursor["confidence"] * 0.5  # Precursor = 50% confidence

            # Boost if also anomalous
            if anomaly_score > 0.5:
                warning_level = "critical"
                confidence = 0.9

        record = {
            "observation": observation,
            "anomaly_score": anomaly_score,
            "warning_level": warning_level,
            "confidence": confidence,
            "precursor": precursor,
            "is_violation": is_violation,
        }

        self.warning_history.append(record)

        return record

    def update_from_violation(self) -> None:
        """Trigger pattern relearning after a violation."""
        self.precursor_learner.learn_precursors()

    def get_early_warning_summary(self) -> Dict:
        """Get summary of early warning system."""
        total_warnings = len(self.warning_history)
        elevated = sum(1 for w in self.warning_history if w["warning_level"] == "elevated")
        critical = sum(1 for w in self.warning_history if w["warning_level"] == "critical")
        violations = sum(1 for w in self.warning_history if w["is_violation"])

        return {
            "boundary_id": self.boundary_id,
            "total_observations": total_warnings,
            "elevated_warnings": elevated,
            "critical_warnings": critical,
            "violations": violations,
            "warning_to_violation_ratio": critical / max(1, violations) if violations > 0 else 0.0,
            "precursor_learning": self.precursor_learner.get_precursor_summary(),
        }
