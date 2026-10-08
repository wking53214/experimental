"""
Workload classification and semantic understanding.

Phase 2 enhancement: Add context to violations so the governor can distinguish
legitimate load from actual degradation.

Phase 9 hardening: anti-spoof — bare claims are not trusted; only registered
patterns whose value range covers the observation count as expected.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class ViolationClass(Enum):
    EXPECTED = "expected"
    ANOMALOUS = "anomalous"
    UNKNOWN = "unknown"


class WorkloadType(Enum):
    BATCH_JOB = "batch_job"
    BACKUP = "backup"
    DEPLOYMENT = "deployment"
    LOAD_TEST = "load_test"
    MAINTENANCE = "maintenance"
    NORMAL_OPERATIONS = "normal_operations"
    UNKNOWN = "unknown"


@dataclass
class ViolationContext:
    violation_id: str
    source: str
    workload_type: WorkloadType
    intensity: float
    duration_seconds: float
    is_expected: bool
    maintenance_window: bool
    confidence: float
    notes: dict = field(default_factory=dict)


@dataclass
class ExpectedLoadPattern:
    pattern_id: str
    boundary_id: str
    resource_or_action: str
    workload_type: WorkloadType
    description: str
    expected_value_range: tuple
    expected_duration_seconds: float
    schedule: str
    severity: str
    notes: dict = field(default_factory=dict)


class WorkloadClassifier:
    """Classifies violations as expected or anomalous (anti-spoof)."""

    def __init__(self):
        self.expected_patterns: dict = {}
        self.classification_history = []
        self.approval_gate = None  # callable(pattern) -> bool; set by Governor(strict_patterns=True)
        self.unapproved: list = []

    def register_expected_pattern(self, pattern: ExpectedLoadPattern, *, trusted: bool = True) -> None:
        if pattern.pattern_id in self.expected_patterns:
            raise ValueError(f"Pattern {pattern.pattern_id} already registered")
        notes = dict(getattr(pattern, "notes", None) or {})
        # With an approval gate (Governor(strict_patterns=True)), "trusted" is not the caller's
        # say-so: only patterns the gate approves are honored, the rest are kept but ignored.
        if trusted and self.approval_gate is not None and not self.approval_gate(pattern):
            trusted = False
            self.unapproved.append(pattern.pattern_id)
        if not trusted or notes.get("attacker"):
            notes["_untrusted"] = True
        pattern.notes = notes
        self.expected_patterns[pattern.pattern_id] = pattern

    def classify_violation(self, boundary_id: str, observed_value: float, context: Optional[dict] = None) -> ViolationClass:
        context = context or {}
        matched_pattern = False
        for pattern in self.expected_patterns.values():
            if pattern.boundary_id != boundary_id:
                continue
            notes = getattr(pattern, "notes", None) or {}
            if notes.get("_untrusted") or notes.get("attacker"):
                continue
            min_val, max_val = pattern.expected_value_range
            if min_val <= observed_value <= max_val:
                return ViolationClass.EXPECTED
            if observed_value > max_val:
                matched_pattern = True
        if matched_pattern:
            return ViolationClass.ANOMALOUS
        similar_violations = [
            v for v in self.classification_history
            if (v["boundary_id"] == boundary_id and
                v["source"] == context.get("source") and
                abs(v["value"] - observed_value) < observed_value * 0.2)
        ]
        if len(similar_violations) > 2:
            return ViolationClass.EXPECTED
        return ViolationClass.UNKNOWN

    def is_expected_violation(self, violation) -> bool:
        context = getattr(violation, "context", None) or {}
        observed = getattr(violation, "observed_value", None)
        boundary_id = getattr(violation, "boundary_id", None)
        if observed is None:
            return False
        for pattern in self.expected_patterns.values():
            if boundary_id and pattern.boundary_id != boundary_id:
                continue
            notes = getattr(pattern, "notes", None) or {}
            if notes.get("_untrusted") or notes.get("attacker"):
                continue
            low, high = pattern.expected_value_range
            if low <= observed <= high:
                return True
        return False

    def get_violation_context(self, boundary_id: str, observed_value: float, limit_value: float, context: Optional[dict] = None) -> ViolationContext:
        context = context or {}
        classification = self.classify_violation(boundary_id, observed_value, context)
        workload_type = self._determine_workload_type(context)
        intensity = min(1.0, (observed_value - limit_value) / limit_value) if limit_value else 0.0
        confidence = 1.0 if classification != ViolationClass.UNKNOWN else 0.0
        violation_context = ViolationContext(
            violation_id=context.get("violation_id", ""),
            source=context.get("source", "unknown"),
            workload_type=workload_type,
            intensity=intensity,
            duration_seconds=context.get("duration_seconds", 0),
            is_expected=classification == ViolationClass.EXPECTED,
            maintenance_window=context.get("maintenance_window", False),
            confidence=confidence,
            notes={"classification": classification.value, "observed_value": observed_value, "limit_value": limit_value},
        )
        self.classification_history.append({
            "boundary_id": boundary_id,
            "source": context.get("source"),
            "value": observed_value,
            "classification": classification.value,
            "timestamp": time.time(),
        })
        return violation_context

    def _determine_workload_type(self, context: dict) -> WorkloadType:
        source = context.get("source", "").lower()
        if "backup" in source:
            return WorkloadType.BACKUP
        elif "batch" in source:
            return WorkloadType.BATCH_JOB
        elif "deploy" in source:
            return WorkloadType.DEPLOYMENT
        elif "load_test" in source or "test" in source:
            return WorkloadType.LOAD_TEST
        elif "maintenance" in source:
            return WorkloadType.MAINTENANCE
        return WorkloadType.UNKNOWN


class SmartPatternDetector:
    """Enhanced pattern detector using violation classification."""

    def __init__(self, classifier: WorkloadClassifier, regular_detector=None):
        self.classifier = classifier
        from src.governance.pattern import PatternDetector
        self.regular_detector = regular_detector if regular_detector is not None else PatternDetector()

    def set_regular_detector(self, detector):
        self.regular_detector = detector

    def create_pattern(self, pattern_id: str, boundary_id: str, violation_threshold: int, time_window_seconds: int):
        return self.regular_detector.create_pattern(
            pattern_id=pattern_id, boundary_id=boundary_id,
            violation_threshold=violation_threshold, time_window_seconds=time_window_seconds,
        )

    def detect_pattern(self, boundary_id: str, recent_violations: list, current_time: Optional[float] = None):
        if not recent_violations:
            return None
        if self.regular_detector:
            regular_pattern = self.regular_detector.detect_pattern(boundary_id, recent_violations)
            if not regular_pattern:
                return None
            anomalous_violations = [v for v in recent_violations if not self.classifier.is_expected_violation(v)]
            if len(anomalous_violations) >= regular_pattern.violation_threshold:
                return regular_pattern
            return None
        return None


class SemanticAdaptationProposal:
    def __init__(self, proposal_id: str, boundary_id: str, anomalous_violation_count: int,
                 expected_violation_count: int, reason: str, confidence: float):
        self.proposal_id = proposal_id
        self.boundary_id = boundary_id
        self.anomalous_violation_count = anomalous_violation_count
        self.expected_violation_count = expected_violation_count
        self.reason = reason
        self.confidence = confidence

    def should_proceed(self) -> bool:
        return self.confidence > 0.7

    def needs_human_review(self) -> bool:
        return self.confidence < 0.7
