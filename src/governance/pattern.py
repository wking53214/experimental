"""
Deterministic pattern detection.

Phase 1 uses a simple, explicit pattern: N violations of the same boundary
within a time window T. No machine learning, no heuristics.
"""
from dataclasses import dataclass
from typing import Optional
import time


@dataclass
class PatternDefinition:
    """
    Explicit pattern definition.

    No ambiguity: if violations reach threshold, pattern detected.
    """
    pattern_id: str
    boundary_id: str
    violation_threshold: int
    time_window_seconds: int

    def is_pattern_detected(
        self,
        recent_violations: list,
        current_time: float,
    ) -> bool:
        """
        Check if pattern threshold is met.

        Args:
            recent_violations: List of violation events for this boundary
            current_time: Current timestamp

        Returns:
            True if N violations occurred within time window
        """
        if len(recent_violations) < self.violation_threshold:
            return False

        # Check most recent violations within time window
        window_start = current_time - self.time_window_seconds
        violations_in_window = [
            v for v in recent_violations
            if v.timestamp >= window_start
        ]

        return len(violations_in_window) >= self.violation_threshold


class PatternDetector:
    """
    Detects patterns in boundary violations.

    This is deterministic: no ML, no scoring, no uncertainty.
    Either the pattern is present or it is not.
    """

    def __init__(self):
        self.patterns: dict[str, PatternDefinition] = {}
        self._detection_cache = {}

    def create_pattern(
        self,
        pattern_id: str,
        boundary_id: str,
        violation_threshold: int,
        time_window_seconds: int,
    ) -> PatternDefinition:
        """Create a new pattern definition."""
        if pattern_id in self.patterns:
            raise ValueError(f"Pattern {pattern_id} already exists")

        pattern = PatternDefinition(
            pattern_id=pattern_id,
            boundary_id=boundary_id,
            violation_threshold=violation_threshold,
            time_window_seconds=time_window_seconds,
        )
        self.patterns[pattern_id] = pattern
        return pattern

    def get_pattern(self, pattern_id: str) -> PatternDefinition:
        """Retrieve a pattern definition."""
        if pattern_id not in self.patterns:
            raise KeyError(f"Pattern {pattern_id} not found")
        return self.patterns[pattern_id]

    def detect_pattern(
        self,
        boundary_id: str,
        recent_violations: list,
        current_time: Optional[float] = None,
    ) -> Optional[PatternDefinition]:
        """
        Check if a pattern is detected for a boundary.

        Returns the pattern if detected, None otherwise.
        """
        if current_time is None:
            current_time = time.time()

        # Find pattern for this boundary
        for pattern_id, pattern in self.patterns.items():
            if pattern.boundary_id == boundary_id:
                if pattern.is_pattern_detected(recent_violations, current_time):
                    return pattern

        return None
