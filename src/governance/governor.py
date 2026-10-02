"""
The Governor: orchestrates the governance loop.

This is the main state machine:

    GOVERNANCE PRINCIPLE
            ↓
       BOUNDARY
            ↓
       EXECUTION
            ↓
      VIOLATION EVENT
            ↓
         PATTERN
            ↓
      ADAPTATION PROPOSAL
            ↓
       AUTHORITY CHECK
            ↓
      TIGHTEN / HUMAN REVIEW
            ↓
        NEW BOUNDARY
            ↓
         VALIDATION
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
    """
    The governor orchestrates the self-hardening governance loop.

    It manages:
    1. Immutable governance principles
    2. Versioned, adaptive boundaries
    3. Execution and violation events
    4. Pattern detection
    5. Adaptation proposals
    6. Authority enforcement
    7. Post-adaptation validation
    """

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

    def ingest_metrics(self, boundary_id: str, timestamp: float, metrics: dict) -> tuple:
        if boundary_id not in self.detector_pipelines:
            self.detector_pipelines[boundary_id] = DetectorPipeline(boundary_id)
        pipeline = self.detector_pipelines[boundary_id]
        pipeline.ingest_metrics(timestamp, metrics)
        detection_result = pipeline.detect_anomalies()
        violations = []
        if detection_result.get("anomaly_detected"):
            try:
                boundary = self.boundaries.get_boundary(boundary_id)
                execution = self.events.record_execution(
                    boundary_id=boundary_id, boundary_version=boundary.version,
                    observed_value=detection_result["anomaly_score"],
                    context={"detection_type": "multi_metric", "signals": detection_result.get("signals")},
                )
                violation = self.events.record_violation(
                    execution_id=execution.execution_id, boundary_id=boundary_id,
                    boundary_version=boundary.version,
                    observed_value=detection_result["anomaly_score"],
                    limit_value=0.85, context=detection_result,
                )
                violations.append({
                    "violation_id": violation.violation_id,
                    "boundary_id": boundary_id,
                    "anomaly_score": detection_result["anomaly_score"],
                })
            except Exception:
                pass
        return detection_result, violations if violations else None

    def execute_against_boundary(self, boundary_id: str, observed_value: Any, context: Optional[dict] = None):
        try:
            boundary = self.boundaries.get_boundary(boundary_id)
        except KeyError:
            raise ValueError(f"Boundary {boundary_id} not found")
        execution = self.events.record_execution(
            boundary_id=boundary_id, boundary_version=boundary.version,
            observed_value=observed_value, context=context,
        )
        self.file_store.write_execution_event(execution.execution_id, {
            "execution_id": execution.execution_id, "boundary_id": execution.boundary_id,
            "boundary_version": execution.boundary_version, "observed_value": str(observed_value),
            "timestamp": execution.timestamp, "context": execution.context,
        })
        if boundary_id not in self.anomaly_detectors:
            self.anomaly_detectors[boundary_id] = AdaptiveAnomalyDetector(boundary_id)
        anomaly_result = None
        try:
            anomaly_result = self.anomaly_detectors[boundary_id].detect_anomaly(observed_value, execution.timestamp)
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
            violation_data = {
                "violation_id": violation.violation_id, "execution_id": violation.execution_id,
                "boundary_id": violation.boundary_id, "boundary_version": violation.boundary_version,
                "observed_value": str(violation.observed_value), "limit_value": str(violation.limit_value),
                "timestamp": violation.timestamp, "context": violation.context,
            }
            if anomaly_result:
                violation_data["anomaly_score"] = f"{anomaly_result.anomaly_score:.4f}"
                violation_data["is_anomaly"] = anomaly_result.is_anomaly
            self.file_store.write_violation_event(violation.violation_id, violation_data)
        return execution, violation

    def detect_and_propose_adaptation(self, boundary_id: str) -> Optional[AdaptationProposal]:
        violations = self.events.get_violations_for_boundary(boundary_id)
        if not violations:
            return None
        pattern_detected = False
        pattern_obj = None
        boundary_count = len(self.boundaries.list_boundaries())
        if self.adaptive_threshold_enabled and boundary_count >= 20:
            adapted_threshold = max(2, min(3, boundary_count // 25))
        else:
            adapted_threshold = 3
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
            reason = f"Pattern detected: {anomalous_count} anomalous violations (+ {expected_count} expected) in {len(violations)} total"
        else:
            reason = f"Pattern detected: {len(violations)} violations"
        proposal = self.proposals.create_proposal(
            boundary_id=boundary_id,
            source_evidence=[v.violation_id for v in violations[-3:]],
            current_value=boundary.current_limit,
            proposed_value=new_limit,
            reason=reason,
            direction=AdaptationDirection.TIGHTEN,
        )
        self.file_store.write_proposal(proposal.proposal_id, {
            "proposal_id": proposal.proposal_id, "boundary_id": proposal.boundary_id,
            "current_value": str(proposal.current_value), "proposed_value": str(proposal.proposed_value),
            "reason": proposal.reason, "direction": proposal.direction.value,
            "status": proposal.status.value, "created_at": proposal.created_at,
        })
        return proposal

    def authorize_proposal(self, proposal: AdaptationProposal):
        result = self.authority.authorize_proposal(proposal)
        decision = self.authority.decisions[-1]
        self.file_store.write_decision(f"{proposal.proposal_id}_{int(time.time()*1000)}", {
            "proposal_id": decision.proposal_id, "direction": decision.direction.value,
            "result": decision.result.value, "reason": decision.reason, "timestamp": decision.timestamp,
        })
        if result == AuthorizationResult.AUTO_APPROVED:
            approved_proposal = self.proposals.mark_approved(proposal.proposal_id)
            return approved_proposal, result
        return proposal, result

    def apply_approved_proposal(self, proposal: AdaptationProposal) -> BoundaryVersion:
        try:
            current = self.proposals.get_proposal(proposal.proposal_id)
        except KeyError:
            current = proposal
        if current.status.value != "approved":
            raise ValueError(
                f"Proposal {current.proposal_id} is not approved. Status: {current.status.value}"
            )
        new_version = self.boundaries.update_boundary(
            boundary_id=current.boundary_id, new_limit=current.proposed_value,
        )
        self.proposals.mark_applied(current.proposal_id)
        return new_version

    def validate_adaptation(self, proposal: AdaptationProposal, observed_state: Any) -> ValidationOutcome:
        outcome = self.validators.validate(proposal.boundary_id, observed_state)
        self.validations.record_validation(
            proposal_id=proposal.proposal_id, boundary_id=proposal.boundary_id,
            expected_condition=f"Violations reduced with new limit {proposal.proposed_value}",
            observed_condition=f"System state: {observed_state}", outcome=outcome,
        )
        return outcome

    def verify_governance_integrity(self):
        checks = [
            ("Principles immutable", self.principles.verify_principle_integrity()),
            ("No auto-loosen", self.authority.verify_no_auto_loosen()),
            ("No auto-disable", self.authority.verify_no_auto_disable()),
            ("File store immutability", self.file_store.verify_immutability()),
        ]
        all_pass = all(result for _, result in checks)
        return all_pass, checks

    def get_status(self) -> dict:
        return {
            "boundaries": len(self.boundaries.list_boundaries()),
            "executions": len(self.events.get_all_executions()),
            "violations": len(self.events.get_all_violations()),
            "proposals": len(self.proposals.get_all_proposals()),
            "validations": len(self.validations.get_all_validations()),
            "principles": len(self.principles.list_principles()),
        }
