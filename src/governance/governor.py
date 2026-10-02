"""
The Governor: orchestrates the governance loop.

HARDENED VERSION - see local experimental-v4 for full file.
This is a temporary stub to restore the repo from placeholder.
"""
from typing import Optional, Any
import time

from .principle import PrincipleStore, GovernancePrinciple, PrincipleType
from .boundary import BoundaryStore, BoundaryVersion
from .event import EventStore, ExecutionEvent, ViolationEvent, ExecutionOutcome
from .pattern import PatternDetector, PatternDefinition
from .proposal import ProposalStore, AdaptationProposal, AdaptationDirection, ProposalGenerator
from .authority import AuthorityModel, AuthorizationResult
from .validation import ValidatorOracle, ValidationStore, ValidationOutcome
from .store import ImmutableFileStore
from .workload import WorkloadClassifier, SmartPatternDetector, ViolationContext
from .metrics import (
    MetricsTracker, EffectivenessOutcome, MetricStream,
    BaselineEstablisher, CorrelationAnalyzer, DetectorPipeline
)
from .baseline import BaselineComparator
from .anomaly_detector import AdaptiveAnomalyDetector


class Governor:
    """Self-hardening governance loop with asymmetric authority."""

    def __init__(self, store_path: str = "/tmp/governance_events", use_semantic: bool = False):
        self.principles = PrincipleStore()
        self.boundaries = BoundaryStore()
        self.events = EventStore()
        self.patterns = PatternDetector()
        self.proposals = ProposalStore()
        self.authority = AuthorityModel()
        self.validators = ValidatorOracle()
        self.validations = ValidationStore()
        self.use_semantic = use_semantic
        self.classifier = WorkloadClassifier() if use_semantic else None
        self.smart_patterns = SmartPatternDetector(self.classifier, self.patterns) if use_semantic else None
        self.metrics_tracker = MetricsTracker()
        self.baseline = BaselineComparator()
        self.global_violation_count = 0
        self.adaptive_threshold_enabled = True
        self.anomaly_detectors = {}
        self.use_anomaly_scoring = True
        self.metric_streams = {}
        self.baseline_establishers = {}
        self.correlation_analyzers = {}
        self.detector_pipelines = {}
        self.proposal_generators = {}
        self.file_store = ImmutableFileStore(store_path)

    def execute_against_boundary(self, boundary_id: str, observed_value: Any, context: Optional[dict] = None):
        boundary = self.boundaries.get_boundary(boundary_id)
        execution = self.events.record_execution(
            boundary_id=boundary_id, boundary_version=boundary.version,
            observed_value=observed_value, context=context,
        )
        if boundary_id not in self.anomaly_detectors:
            self.anomaly_detectors[boundary_id] = AdaptiveAnomalyDetector(boundary_id)
        try:
            self.anomaly_detectors[boundary_id].detect_anomaly(observed_value, execution.timestamp)
        except (TypeError, ValueError):
            pass
        violation = None
        try:
            is_violation = observed_value > boundary.current_limit
        except TypeError:
            is_violation = False
        if is_violation:
            violation = self.events.record_violation(
                execution_id=execution.execution_id, boundary_id=boundary_id,
                boundary_version=boundary.version, observed_value=observed_value,
                limit_value=boundary.current_limit, context=context,
            )
        return execution, violation

    def detect_and_propose_adaptation(self, boundary_id: str) -> Optional[AdaptationProposal]:
        violations = self.events.get_violations_for_boundary(boundary_id)
        if not violations:
            return None
        boundary_count = len(self.boundaries.list_boundaries())
        if self.adaptive_threshold_enabled and boundary_count >= 20:
            adapted_threshold = max(2, min(3, boundary_count // 25))
        else:
            adapted_threshold = 3
        pattern_detected = False
        if self.use_semantic and self.smart_patterns:
            pattern_obj = self.smart_patterns.detect_pattern(boundary_id=boundary_id, recent_violations=violations)
            pattern_detected = pattern_obj is not None
            if not pattern_detected:
                anomalous = [v for v in violations if not (self.classifier and self.classifier.is_expected_violation(v))]
                if len(anomalous) >= adapted_threshold:
                    pattern_detected = True
        else:
            if self.adaptive_threshold_enabled and len(violations) >= adapted_threshold:
                pattern_detected = True
            if not pattern_detected:
                pattern_obj = self.patterns.detect_pattern(boundary_id=boundary_id, recent_violations=violations)
                pattern_detected = pattern_obj is not None
        if not pattern_detected:
            return None
        boundary = self.boundaries.get_boundary(boundary_id)
        try:
            hist = self.boundaries.boundaries.get(boundary_id)
            if hist and hist.versions:
                first_ver = min(hist.versions.keys())
                original_limit = hist.versions[first_ver].current_limit
            else:
                original_limit = boundary.current_limit
        except Exception:
            original_limit = boundary.current_limit
        floor = float(original_limit) * 0.20 if original_limit else 0.0
        new_limit = float(boundary.current_limit) * 0.9
        if new_limit < floor:
            return None
        if self.use_semantic and self.classifier:
            expected_count = sum(1 for v in violations if self.classifier.is_expected_violation(v))
            anomalous_count = len(violations) - expected_count
            if anomalous_count == 0:
                return None
            reason = f"Pattern detected: {anomalous_count} anomalous (+ {expected_count} expected)"
        else:
            reason = f"Pattern detected: {len(violations)} violations"
        return self.proposals.create_proposal(
            boundary_id=boundary_id,
            source_evidence=[v.violation_id for v in violations[-3:]],
            current_value=boundary.current_limit,
            proposed_value=new_limit,
            reason=reason,
            direction=AdaptationDirection.TIGHTEN,
        )

    def authorize_proposal(self, proposal: AdaptationProposal):
        result = self.authority.authorize_proposal(proposal)
        if result == AuthorizationResult.AUTO_APPROVED:
            approved = self.proposals.mark_approved(proposal.proposal_id)
            return approved, result
        return proposal, result

    def apply_approved_proposal(self, proposal: AdaptationProposal) -> BoundaryVersion:
        try:
            current = self.proposals.get_proposal(proposal.proposal_id)
        except KeyError:
            current = proposal
        if current.status.value != "approved":
            raise ValueError(f"Proposal {current.proposal_id} is not approved. Status: {current.status.value}")
        new_version = self.boundaries.update_boundary(
            boundary_id=current.boundary_id, new_limit=current.proposed_value,
        )
        self.proposals.mark_applied(current.proposal_id)
        return new_version

    def get_status(self) -> dict:
        return {
            "boundaries": len(self.boundaries.list_boundaries()),
            "executions": len(self.events.get_all_executions()),
            "violations": len(self.events.get_all_violations()),
            "proposals": len(self.proposals.get_all_proposals()),
        }
