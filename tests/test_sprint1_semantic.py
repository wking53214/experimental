"""
Phase 2 Sprint 1: Semantic Understanding Tests

These tests demonstrate how semantic understanding fixes the blind pattern
detection problems exposed in Phase 1 TEST 7 & 8.

Key achievement: System can now distinguish legitimate high-load from degradation.
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.workload import (
    WorkloadClassifier, SmartPatternDetector, ExpectedLoadPattern,
    ViolationClass, WorkloadType
)


class TestSemanticClassification:
    """Test basic workload classification."""

    def test_classify_expected_violation(self):
        """System recognizes expected high-load as legitimate."""
        classifier = WorkloadClassifier()

        # Register backup pattern: 1000-1200 MB expected on Sundays
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup_pattern",
                boundary_id="memory_limit",
                resource_or_action="memory_mb",
                workload_type=WorkloadType.BACKUP,
                description="Daily backup job",
                expected_value_range=(1000, 1200),
                expected_duration_seconds=600,
                schedule="Daily 2-3am",
                severity="normal",
            )
        )

        # Classify violation matching pattern
        classification = classifier.classify_violation(
            boundary_id="memory_limit",
            observed_value=1100,
            context={"source": "backup_service"}
        )

        assert classification == ViolationClass.EXPECTED

    def test_classify_anomalous_violation(self):
        """System marks unexpected high-load as anomalous."""
        classifier = WorkloadClassifier()

        # Register backup pattern
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup_pattern",
                boundary_id="memory_limit",
                resource_or_action="memory_mb",
                workload_type=WorkloadType.BACKUP,
                description="Daily backup job",
                expected_value_range=(1000, 1200),
                expected_duration_seconds=600,
                schedule="Daily 2-3am",
                severity="normal",
            )
        )

        # Classify violation exceeding expected range
        classification = classifier.classify_violation(
            boundary_id="memory_limit",
            observed_value=1600,  # Exceeds expected 1200
            context={"source": "unknown"}
        )

        assert classification == ViolationClass.ANOMALOUS

    def test_classify_maintenance_window(self):
        """System recognizes maintenance windows."""
        classifier = WorkloadClassifier()

        # Even without registered pattern, maintenance window is expected
        classification = classifier.classify_violation(
            boundary_id="cpu_limit",
            observed_value=95,
            context={
                "source": "unknown",
                "maintenance_window": True
            }
        )

        assert classification == ViolationClass.EXPECTED

    def test_unknown_classification(self):
        """System classifies unknown violations conservatively."""
        classifier = WorkloadClassifier()

        classification = classifier.classify_violation(
            boundary_id="cpu_limit",
            observed_value=95,
            context={"source": "unknown"}
        )

        assert classification == ViolationClass.UNKNOWN


class TestSmartPatternDetection:
    """Test smart pattern detection that filters expected violations."""

    def test_smart_pattern_ignores_expected_violations(self):
        """
        Smart detector filters expected violations from pattern detection.

        This solves TEST 7: Legitimate high-load no longer triggers false-positive
        tightening because expected violations are filtered out.
        """
        classifier = WorkloadClassifier()
        detector = SmartPatternDetector(classifier)

        # Register expected backup pattern
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="cpu_usage",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.BACKUP,
                description="Daily backup",
                expected_value_range=(70, 85),
                expected_duration_seconds=600,
                schedule="Daily",
                severity="normal",
            )
        )

        # Create pattern detector requiring 3 violations in 30s
        detector.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_usage",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Create mock violations
        class MockViolation:
            def __init__(self, value, source, timestamp=None):
                self.observed_value = value
                self.context = {"source": source}
                self.timestamp = timestamp or time.time()

        # Generate 3 violations: all from expected backup source
        violations = [
            MockViolation(75, "backup_service", time.time()),
            MockViolation(78, "backup_service", time.time() + 1),
            MockViolation(80, "backup_service", time.time() + 2),
        ]

        # Smart detector should NOT detect pattern (all expected)
        detected = detector.detect_pattern(
            boundary_id="cpu_usage",
            recent_violations=violations,
        )

        assert not detected, "Expected violations should not trigger pattern detection"

    def test_smart_pattern_detects_anomalous_violations(self):
        """Smart detector correctly identifies anomalous patterns."""
        classifier = WorkloadClassifier()
        detector = SmartPatternDetector(classifier)

        # Register expected backup pattern
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="cpu_usage",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.BACKUP,
                description="Daily backup",
                expected_value_range=(70, 85),
                expected_duration_seconds=600,
                schedule="Daily",
                severity="normal",
            )
        )

        # Create pattern detector
        detector.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_usage",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Create mock violations: anomalous (from unknown source)
        class MockViolation:
            def __init__(self, value, source, timestamp=None):
                self.observed_value = value
                self.context = {"source": source}
                self.timestamp = timestamp or time.time()

        violations = [
            MockViolation(95, "unknown_service", time.time()),
            MockViolation(92, "unknown_service", time.time() + 1),
            MockViolation(94, "unknown_service", time.time() + 2),
        ]

        # Smart detector SHOULD detect pattern (anomalous)
        detected = detector.detect_pattern(
            boundary_id="cpu_usage",
            recent_violations=violations,
        )

        assert detected, "Anomalous violations should trigger pattern detection"

    def test_mixed_violations_partial_detection(self):
        """
        Test with mixed expected and anomalous violations.

        Pattern should only be detected if anomalous violations reach threshold.
        """
        classifier = WorkloadClassifier()
        detector = SmartPatternDetector(classifier)

        # Register backup pattern
        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="cpu_usage",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.BACKUP,
                description="Daily backup",
                expected_value_range=(70, 85),
                expected_duration_seconds=600,
                schedule="Daily",
                severity="normal",
            )
        )

        detector.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_usage",
            violation_threshold=2,
            time_window_seconds=30,
        )

        class MockViolation:
            def __init__(self, value, source, timestamp=None):
                self.observed_value = value
                self.context = {"source": source}
                self.timestamp = timestamp or time.time()

        # Mix: 1 expected (backup) + 2 anomalous (unknown)
        violations = [
            MockViolation(75, "backup_service", time.time()),      # Expected
            MockViolation(95, "unknown", time.time() + 1),          # Anomalous
            MockViolation(94, "unknown", time.time() + 2),          # Anomalous
        ]

        # Smart detector should detect (2 anomalous >= threshold)
        detected = detector.detect_pattern(
            boundary_id="cpu_usage",
            recent_violations=violations,
        )

        assert detected, "Should detect when anomalous violations meet threshold"


class TestGovernorWithSemantic:
    """Test Governor with semantic understanding enabled."""

    def test_governor_semantic_prevents_false_positive(self):
        """
        PHASE 2 FIX for TEST 7:

        Legitimate workload (database migration) that consistently violates
        initial boundary is no longer blindly tightened.
        """
        governor = Governor(store_path="/tmp/test_semantic_1", use_semantic=True)

        # Create boundary with conservative limit
        governor.boundaries.create_boundary(
            boundary_id="cpu_allocation",
            resource_or_action="cpu_percent",
            initial_limit=60,  # Conservative
        )

        # Register that migrations are expected high-load
        governor.classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="migration_pattern",
                boundary_id="cpu_allocation",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.BATCH_JOB,
                description="Database migration",
                expected_value_range=(65, 80),
                expected_duration_seconds=3600,
                schedule="On-demand",
                severity="elevated",
            )
        )

        # Create smart pattern detector
        governor.smart_patterns.create_pattern(
            pattern_id="cpu_pattern",
            boundary_id="cpu_allocation",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Simulate legitimate database migration using 70% CPU
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="cpu_allocation",
                observed_value=70,
                context={
                    "source": "batch_processor",  # Use known source
                    "workload": "database_migration",
                    "reason": "legitimate"
                }
            )
            time.sleep(0.1)

        # Check if pattern detected
        proposal = governor.detect_and_propose_adaptation("cpu_allocation")

        # With semantic understanding: NO proposal (expected violations filtered)
        assert proposal is None, \
            "Semantic understanding should prevent proposal for expected load"

        # Verify boundary unchanged
        boundary = governor.boundaries.get_boundary("cpu_allocation")
        assert boundary.version == 1, "Boundary should not be tightened"
        assert boundary.current_limit == 60, "Limit should remain unchanged"

    def test_governor_semantic_detects_anomalous(self):
        """Semantic understanding still detects actual anomalies."""
        governor = Governor(store_path="/tmp/test_semantic_2", use_semantic=True)

        # Create boundary
        governor.boundaries.create_boundary(
            boundary_id="memory_usage",
            resource_or_action="memory_mb",
            initial_limit=1000,
        )

        # Register expected pattern: backups 1100-1200 MB
        governor.classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="memory_usage",
                resource_or_action="memory_mb",
                workload_type=WorkloadType.BACKUP,
                description="Backup",
                expected_value_range=(1100, 1200),
                expected_duration_seconds=600,
                schedule="Daily",
                severity="normal",
            )
        )

        governor.smart_patterns.create_pattern(
            pattern_id="memory_pattern",
            boundary_id="memory_usage",
            violation_threshold=3,
            time_window_seconds=30,
        )

        # Generate anomalous violations (from unknown source, exceeding expected)
        for i in range(3):
            governor.execute_against_boundary(
                boundary_id="memory_usage",
                observed_value=1600,  # Anomalous (exceeds expected range)
                context={"source": "unknown_process"}
            )
            time.sleep(0.1)

        # Pattern SHOULD be detected
        proposal = governor.detect_and_propose_adaptation("memory_usage")

        assert proposal is not None, "Should detect anomalous violations"
        assert proposal.direction.value == "tighten"


class TestSemanticEnablingToggle:
    """Test that semantic understanding can be toggled on/off."""

    def test_semantic_disabled_uses_phase1_logic(self):
        """When disabled, reverts to Phase 1 blind detection."""
        governor = Governor(store_path="/tmp/test_semantic_3", use_semantic=False)

        assert governor.use_semantic == False
        assert governor.classifier is None
        assert governor.smart_patterns is None

    def test_semantic_enabled_uses_phase2_logic(self):
        """When enabled, uses Phase 2 semantic understanding."""
        governor = Governor(store_path="/tmp/test_semantic_4", use_semantic=True)

        assert governor.use_semantic == True
        assert governor.classifier is not None
        assert governor.smart_patterns is not None


class TestViolationContextEnrichment:
    """Test enriching violations with semantic context."""

    def test_context_enrichment_expected(self):
        """Enrich violation context for expected violations."""
        classifier = WorkloadClassifier()

        classifier.register_expected_pattern(
            ExpectedLoadPattern(
                pattern_id="backup",
                boundary_id="cpu",
                resource_or_action="cpu_percent",
                workload_type=WorkloadType.BACKUP,
                description="Backup",
                expected_value_range=(70, 85),
                expected_duration_seconds=600,
                schedule="Daily",
                severity="normal",
            )
        )

        context = classifier.get_violation_context(
            boundary_id="cpu",
            observed_value=75,
            limit_value=60,
            context={"source": "backup_service"}
        )

        assert context.is_expected == True
        assert context.workload_type == WorkloadType.BACKUP
        assert context.intensity > 0  # (75 - 60) / 60

    def test_context_enrichment_anomalous(self):
        """Enrich violation context for anomalous violations."""
        classifier = WorkloadClassifier()

        context = classifier.get_violation_context(
            boundary_id="cpu",
            observed_value=95,
            limit_value=60,
            context={"source": "unknown"}
        )

        assert context.is_expected == False
        assert context.intensity > 0.5  # (95 - 60) / 60 ≈ 0.583
