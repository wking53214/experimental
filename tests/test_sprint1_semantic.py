"""Sprint 1: Semantic classification tests (anti-spoof hardened)."""
import pytest
from src.governance.workload import (
    WorkloadClassifier, ExpectedLoadPattern, WorkloadType, ViolationClass,
    SmartPatternDetector,
)
from src.governance.pattern import PatternDetector


class TestSemanticClassification:
    def test_classify_expected_with_pattern(self):
        classifier = WorkloadClassifier()
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="memory_limit",
                resource_or_action="memory_mb",
                workload_type=WorkloadType.BACKUP,
                description="Nightly backup",
                expected_value_range=(1000, 1200),
                expected_duration_seconds=3600,
                schedule="Daily 2am",
                severity="elevated",
            )
        )
        classification = classifier.classify_violation(
            boundary_id="memory_limit", observed_value=1100,
            context={"source": "backup_service"},
        )
        assert classification == ViolationClass.EXPECTED

    def test_classify_anomalous_exceeds_pattern(self):
        classifier = WorkloadClassifier()
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="memory_limit",
                resource_or_action="memory_mb",
                workload_type=WorkloadType.BACKUP,
                description="Nightly backup",
                expected_value_range=(1000, 1200),
                expected_duration_seconds=3600,
                schedule="Daily 2am",
                severity="elevated",
            )
        )
        classification = classifier.classify_violation(
            boundary_id="memory_limit", observed_value=1600,
            context={"source": "unknown"},
        )
        assert classification == ViolationClass.ANOMALOUS

    def test_classify_maintenance_window(self):
        """Maintenance claims alone are not trusted (anti-spoof); require a registered pattern."""
        classifier = WorkloadClassifier()
        classification = classifier.classify_violation(
            boundary_id="cpu_limit", observed_value=95,
            context={"source": "unknown", "maintenance_window": True},
        )
        assert classification == ViolationClass.UNKNOWN
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="maint",
                boundary_id="cpu_limit",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.MAINTENANCE,
                description="Scheduled maintenance",
                expected_value_range=(80, 100),
                expected_duration_seconds=600,
                schedule="Weekly",
                severity="elevated",
            )
        )
        classification = classifier.classify_violation(
            boundary_id="cpu_limit", observed_value=95,
            context={"source": "unknown", "maintenance_window": True},
        )
        assert classification == ViolationClass.EXPECTED

    def test_unknown_classification(self):
        classifier = WorkloadClassifier()
        classification = classifier.classify_violation(
            boundary_id="cpu_limit", observed_value=95,
            context={"source": "unknown"},
        )
        assert classification == ViolationClass.UNKNOWN


class TestSmartPatternDetection:
    def test_smart_pattern_ignores_expected_violations(self):
        classifier = WorkloadClassifier()
        detector = PatternDetector()
        smart = SmartPatternDetector(classifier, detector)
        smart.create_pattern("p1", "cpu", 3, 30)
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="ok", boundary_id="cpu", resource_or_action="cpu",
                workload_type=WorkloadType.MAINTENANCE, description="ok",
                expected_value_range=(0, 200), expected_duration_seconds=60,
                schedule="always", severity="low",
            )
        )

        class V:
            def __init__(self, val):
                self.boundary_id = "cpu"
                self.observed_value = val
                self.context = {}
                self.timestamp = 0
        # All expected -> no pattern
        result = smart.detect_pattern("cpu", [V(100), V(110), V(120)])
        assert result is None
