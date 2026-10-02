"""
Workload classification and semantic understanding.

Phase 2 enhancement: Add context to violations so the governor can distinguish
legitimate load from actual degradation.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class ViolationClass(Enum):
    """Classification of a violation event."""
    EXPECTED = "expected"
    ANOMALOUS = "anomalous"
    UNKNOWN = "unknown"


class WorkloadType(Enum):
    """Category of workload generating violations."""
    BATCH_JOB = "batch_job"
    BACKUP = "backup"
    DEPLOYMENT = "deployment"
    LOAD_TEST = "load_test"
    MAINTENANCE = "maintenance"
    NORMAL_OPERATIONS = "normal_operations"
    UNKNOWN = "unknown"


@dataclass
class ViolationContext:
    """Rich context about a violation event."""
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
    """Definition of an expected load pattern."""
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
    """Classifies violations as expected or anomalous."""
    
    def __init__(self):
        self.expected_patterns: dict[str, ExpectedLoadPattern] = {}
        self.classification_history = []
    
    def register_expected_pattern(self, pattern: ExpectedLoadPattern) -> None:
        """Register a known expected load pattern."""
        if pattern.pattern_id in self.expected_patterns:
            raise ValueError(f"Pattern {pattern.pattern_id} already registered")
        self.expected_patterns[pattern.pattern_id] = pattern
    
    def classify_violation(
        self,
        boundary_id: str,
        observed_value: float,
        context: Optional[dict] = None,
    ) -> ViolationClass:
        """Classify a violation as EXPECTED, ANOMALOUS, or UNKNOWN."""
        context = context or {}
        
        # Check against registered expected patterns
        for pattern in self.expected_patterns.values():
            if pattern.boundary_id != boundary_id:
                continue
            
            min_val, max_val = pattern.expected_value_range
            if min_val <= observed_value <= max_val:
                return ViolationClass.EXPECTED
            
            if observed_value > max_val:
                return ViolationClass.ANOMALOUS
        
        # Check if in maintenance window
        if context.get("maintenance_window") == True:
            return ViolationClass.EXPECTED
        
        # Check for known workload sources
        known_sources = ["backup_service", "batch_processor", "deployment_service", "load_test"]
        if context.get("source") in known_sources:
            return ViolationClass.EXPECTED
        
        # Check historical patterns
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
        """Check if a violation is expected."""
        context = violation.context or {}
        
        if context.get("expected") == True:
            return True
        
        if context.get("maintenance_window") == True:
            return True
        
        source = context.get("source", "").lower()
        if any(known in source for known in ["backup", "batch", "maintenance"]):
            return True
        
        return False
    
    def get_violation_context(
        self,
        boundary_id: str,
        observed_value: float,
        limit_value: float,
        context: Optional[dict] = None,
    ) -> ViolationContext:
        """Create enriched context for a violation."""
        context = context or {}
        classification = self.classify_violation(boundary_id, observed_value, context)
        workload_type = self._determine_workload_type(context)
        intensity = min(1.0, (observed_value - limit_value) / limit_value)
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
            notes={
                "classification": classification.value,
                "observed_value": observed_value,
                "limit_value": limit_value,
            },
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
        """Determine workload type from context."""
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
        else:
            return WorkloadType.UNKNOWN


class SmartPatternDetector:
    """Enhanced pattern detector using violation classification.

    Delegates to regular PatternDetector but filters for anomalous violations.
    This prevents false positives from expected load (maintenance, backups, etc)
    while still catching real attacks.
    """

    def __init__(self, classifier: WorkloadClassifier, regular_detector=None):
        self.classifier = classifier
        self.regular_detector = regular_detector

    def set_regular_detector(self, detector):
        """Set reference to the regular PatternDetector."""
        self.regular_detector = detector

    def detect_pattern(
        self,
        boundary_id: str,
        recent_violations: list,
        current_time: Optional[float] = None,
    ):
        """Detect pattern using regular detector, but filter for anomalous violations.

        Falls back to regular detector if patterns aren't registered here.
        Returns the pattern object if detected (like regular detector).
        """
        if not recent_violations:
            return None

        # First, try regular detector (which has the authoritative pattern registry)
        if self.regular_detector:
            regular_pattern = self.regular_detector.detect_pattern(boundary_id, recent_violations)
            if not regular_pattern:
                return None

            # Pattern exists in regular detector - now check if violations are anomalous
            # This prevents false positives from expected load patterns
            anomalous_violations = [
                v for v in recent_violations
                if not self.classifier.is_expected_violation(v)
            ]

            # If we have anomalous violations matching the threshold, pattern is real
            if len(anomalous_violations) >= regular_pattern.violation_threshold:
                return regular_pattern
            else:
                # Violations exist but are all expected (maintenance, backups, etc)
                # No pattern to adapt to
                return None

        # Fallback: if no regular detector set, can't detect anything
        return None


class SemanticAdaptationProposal:
    """Enhanced proposal that includes semantic reasoning."""
    
    def __init__(
        self,
        proposal_id: str,
        boundary_id: str,
        anomalous_violation_count: int,
        expected_violation_count: int,
        reason: str,
        confidence: float,
    ):
        self.proposal_id = proposal_id
        self.boundary_id = boundary_id
        self.anomalous_violation_count = anomalous_violation_count
        self.expected_violation_count = expected_violation_count
        self.reason = reason
        self.confidence = confidence
    
    def should_proceed(self) -> bool:
        """Determine if proposal should proceed based on confidence."""
        return self.confidence > 0.7
    
    def needs_human_review(self) -> bool:
        """Determine if human review is recommended."""
        return self.confidence < 0.7
