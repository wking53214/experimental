"""
Governor — Orchestration of the Self-Hardening Governance Loop

H1 FIXED: Telemetry/store failures append to self.telemetry_errors.
H2 FIXED: Operator path uses AuthorizationResult.OPERATOR_APPROVED.
"""
from typing import Optional, Any
import time

from .principle import PrincipleStore
from .boundary import BoundaryStore, BoundaryVersion
from .event import EventStore
from .pattern import PatternDetector
from .proposal import ProposalStore, AdaptationProposal, AdaptationDirection
from .authority import AuthorityModel, AuthorizationResult
from .grant import is_loosening
from .validation import ValidatorOracle, ValidationStore, ValidationOutcome
from .store import ImmutableFileStore
from .workload import WorkloadClassifier, SmartPatternDetector
from .metrics import MetricsTracker, DetectorPipeline
from .baseline import BaselineComparator
from .anomaly_detector import AdaptiveAnomalyDetector
from .phase9_integration import HybridDetectorPipeline
from .rollback import RollbackExecutor, RollbackReason


class Governor:
    # Circuit breaker: after this many automatic tightenings of one boundary with no
    # human decision in between, further tightenings wait for a human (theory: dampening
    # of false-positive and cascading tightening, white paper sec. 4).
    AUTO_TIGHTEN_LIMIT = 3

    def __init__(self, store_path: str = "/tmp/governance_events", use_semantic: bool = True):
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
        self.rollback = RollbackExecutor(self)
        self.telemetry_errors: list = []

    def _record_telemetry_error(self, where: str, exc: Exception) -> None:
        self.telemetry_errors.append({
            "where": where, "type": type(exc).__name__, "message": str(exc)[:200],
        })

    def ingest_metrics(self, boundary_id: str, timestamp: float, metrics: dict):
        if boundary_id not in self.detector_pipelines:
            self.detector_pipelines[boundary_id] = HybridDetectorPipeline(boundary_id)
        pipeline = self.detector_pipelines[boundary_id]
        pipeline.ingest_metrics(timestamp, metrics)
        detection_result = pipeline.detect_anomalies()
        violations = []
        if detection_result.get("anomaly_detected"):
            try:
                boundary = self.boundaries.get_boundary(boundary_id)
                execution = self.events.record_execution(
                    boundary_id=boundary_id, boundary_version=boundary.version,
                    observed_value=detection_result.get("anomaly_score", 0.0),
                    context={"detection_type": "hybrid_multi_metric"},
                )
                violation = self.events.record_violation(
                    execution_id=execution.execution_id, boundary_id=boundary_id,
                    boundary_version=boundary.version,
                    observed_value=detection_result.get("anomaly_score", 0.0),
                    limit_value=0.85, context=detection_result,
                )
                violations.append({
                    "violation_id": violation.violation_id,
                    "boundary_id": boundary_id,
                    "anomaly_score": detection_result.get("anomaly_score"),
                })
            except Exception as e:
                self._record_telemetry_error("ingest_metrics", e)
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
        try:
            self.file_store.write_execution_event(execution.execution_id, {
                "execution_id": execution.execution_id, "boundary_id": execution.boundary_id,
                "boundary_version": execution.boundary_version, "observed_value": str(observed_value),
                "timestamp": execution.timestamp, "context": execution.context,
            })
        except Exception as e:
            self._record_telemetry_error("write_execution", e)
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
            try:
                self.file_store.write_violation_event(violation.violation_id, {
                    "violation_id": violation.violation_id, "boundary_id": violation.boundary_id,
                    "observed_value": str(violation.observed_value),
                    "limit_value": str(violation.limit_value),
                })
            except Exception as e:
                self._record_telemetry_error("write_violation", e)
        return execution, violation

    def detect_and_propose_adaptation(self, boundary_id: str) -> Optional[AdaptationProposal]:
        violations = self.events.get_violations_for_boundary(boundary_id)
        if not violations:
            return None
        pattern_detected = False
        boundary_count = len(self.boundaries.list_boundaries())
        adapted_threshold = max(2, min(3, boundary_count // 25)) if self.adaptive_threshold_enabled and boundary_count >= 20 else 3
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
            original_limit = hist.versions[min(hist.versions.keys())].current_limit if hist and hist.versions else boundary.current_limit
        except Exception as e:
            self._record_telemetry_error("original_limit", e)
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

    def _auto_tightenings_since_review(self, boundary_id: str) -> int:
        """Automatic tightenings of this boundary since the last human decision on it."""
        count = 0
        for d in reversed(self.authority.decisions):
            if d.decided_by != "system":
                # Any human decision on this boundary's proposals resets the count.
                try:
                    if self.proposals.get_proposal(d.proposal_id).boundary_id == boundary_id:
                        break
                except KeyError:
                    pass
                continue
            if d.result != AuthorizationResult.AUTO_APPROVED:
                continue
            try:
                if self.proposals.get_proposal(d.proposal_id).boundary_id == boundary_id:
                    count += 1
            except KeyError:
                continue
        return count

    def authorize_proposal(self, proposal: AdaptationProposal):
        breaker_open = (
            proposal.direction == AdaptationDirection.TIGHTEN
            and self._auto_tightenings_since_review(proposal.boundary_id) >= self.AUTO_TIGHTEN_LIMIT
        )
        result = self.authority.authorize_proposal(proposal, breaker_open=breaker_open)
        if result == AuthorizationResult.AUTO_APPROVED:
            return self.proposals.mark_approved(proposal.proposal_id), result
        return proposal, result

    def review_effectiveness(self, proposal_id: str, observed_state) -> tuple:
        """Validation layer: judge an applied adaptation against an independent signal.

        If the registered validator says the adaptation DEGRADED things, the boundary is
        rolled back. A rollback that loosens is queued for a human (see RollbackExecutor),
        so the system still never loosens on its own. Returns (outcome, rollback_decision).
        """
        proposal = self.proposals.get_proposal(proposal_id)
        outcome = self.validate_adaptation(proposal, observed_state)
        decision = None
        if outcome == ValidationOutcome.DEGRADED and self.rollback.should_attempt_rollback(
                proposal.boundary_id, "degraded", 1.0):
            decision = self.rollback.execute_rollback(
                proposal_id, proposal.boundary_id, RollbackReason.DEGRADED_METRICS)
        return outcome, decision

    def apply_approved_proposal(self, proposal: AdaptationProposal) -> BoundaryVersion:
        try:
            current = self.proposals.get_proposal(proposal.proposal_id)
        except KeyError:
            current = proposal
        if current.status.value != "approved":
            raise ValueError(f"Proposal {current.proposal_id} is not approved. Status: {current.status.value}")
        # An approved status alone is not authority: the AuthorityModel must have decided.
        decision = self.authority.latest_decision(current.proposal_id)
        if decision is None or decision.result not in (
                AuthorizationResult.AUTO_APPROVED, AuthorizationResult.OPERATOR_APPROVED):
            raise PermissionError(
                f"Proposal {current.proposal_id} has no approving decision from the authority")
        grant = None
        if decision.result == AuthorizationResult.OPERATOR_APPROVED:
            grant = self.authority.grants.get(current.proposal_id)
        else:
            # System approval is only for tightening, judged against the ACTUAL current limit.
            actual = self.boundaries.get_boundary(current.boundary_id).current_limit
            if is_loosening(actual, current.proposed_value):
                raise PermissionError(
                    f"Auto-approved proposal {current.proposal_id} would raise {current.boundary_id} "
                    f"from {actual} to {current.proposed_value}; a human must approve that")
            self._enforce_usability_floor(current.boundary_id, current.proposed_value)
        new_version = self.boundaries.update_boundary(
            boundary_id=current.boundary_id, new_limit=current.proposed_value, grant=grant,
        )
        self.proposals.mark_applied(current.proposal_id)
        return new_version

    def _enforce_usability_floor(self, boundary_id: str, new_limit) -> None:
        """The system may not tighten a boundary below 20% of its original limit."""
        history = self.boundaries.boundaries.get(boundary_id)
        if not history or not history.versions:
            return
        original = history.versions[min(history.versions)].current_limit
        try:
            floor = float(original) * 0.20
            if float(new_limit) < floor:
                raise ValueError(
                    f"Refusing to tighten {boundary_id} to {new_limit}: below the 20% "
                    f"usability floor ({floor})")
        except (TypeError, ValueError) as e:
            if "usability floor" in str(e):
                raise

    def validate_adaptation(self, proposal, observed_state):
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
            ("No unauthorized limit increase (version history audit)",
             not self.boundaries.unauthorized_loosenings()),
            ("File store immutability", self.file_store.verify_immutability()),
        ]
        return all(r for _, r in checks), checks

    def detect_from_pipeline(self, boundary_id: str):
        if boundary_id in self.detector_pipelines:
            pipeline = self.detector_pipelines[boundary_id]
            try:
                result = pipeline.detect_anomalies()
            except Exception as e:
                self._record_telemetry_error("detect_from_pipeline", e)
                result = {}
            if result.get("anomaly_detected"):
                try:
                    boundary = self.boundaries.get_boundary(boundary_id)
                    execution = self.events.record_execution(
                        boundary_id=boundary_id, boundary_version=boundary.version,
                        observed_value=result.get("anomaly_score", 1.0),
                        context={"source": "hybrid_pipeline"},
                    )
                    self.events.record_violation(
                        execution_id=execution.execution_id, boundary_id=boundary_id,
                        boundary_version=boundary.version,
                        observed_value=result.get("anomaly_score", 1.0),
                        limit_value=0.5, context=result,
                    )
                except Exception as e:
                    self._record_telemetry_error("detect_from_pipeline_record", e)
        return self.detect_and_propose_adaptation(boundary_id)

    def submit_for_review(self, proposal):
        if proposal.direction == AdaptationDirection.TIGHTEN:
            raise ValueError("TIGHTEN proposals are not submitted for human review")
        updated = self.proposals.mark_pending_review(proposal.proposal_id)
        try:
            self.file_store.write_decision(
                f"{proposal.proposal_id}_review_{int(time.time()*1000)}",
                {"proposal_id": proposal.proposal_id, "status": "pending_review",
                 "direction": proposal.direction.value, "timestamp": time.time()},
            )
        except Exception as e:
            self._record_telemetry_error("submit_for_review", e)
        return updated

    def list_pending_review(self):
        from .proposal import ProposalStatus
        def escalated_tighten(p):
            # A tightening held by the circuit breaker is waiting for a human too.
            d = self.authority.latest_decision(p.proposal_id)
            return d is not None and d.result == AuthorizationResult.REQUIRES_HUMAN_REVIEW
        return [
            p for p in self.proposals.get_all_proposals()
            if p.status == ProposalStatus.PENDING_REVIEW
            or (p.status == ProposalStatus.PENDING and p.direction.value in ("loosen", "disable"))
            or (p.status == ProposalStatus.PENDING and escalated_tighten(p))
        ]

    def get_evidence_pack(self, proposal_id: str) -> dict:
        proposal = self.proposals.get_proposal(proposal_id)
        boundary = None
        version_chain = []
        try:
            boundary = self.boundaries.get_boundary(proposal.boundary_id)
            hist = self.boundaries.boundaries.get(proposal.boundary_id)
            if hist and hist.versions:
                for v in sorted(hist.versions.keys()):
                    ver = hist.versions[v]
                    version_chain.append({
                        "version": ver.version, "limit": ver.current_limit,
                        "status": ver.status.value if hasattr(ver.status, "value") else str(ver.status),
                    })
        except Exception as e:
            self._record_telemetry_error("evidence_pack", e)
        violations = self.events.get_violations_for_boundary(proposal.boundary_id)
        recent = violations[-10:] if violations else []
        return {
            "proposal": {
                "proposal_id": proposal.proposal_id, "boundary_id": proposal.boundary_id,
                "direction": proposal.direction.value, "current_value": proposal.current_value,
                "proposed_value": proposal.proposed_value, "reason": proposal.reason,
                "status": proposal.status.value,
            },
            "boundary_current": boundary.current_limit if boundary else None,
            "version_chain": version_chain,
            "recent_violations": [{
                "violation_id": getattr(v, "violation_id", None),
                "observed_value": getattr(v, "observed_value", None),
                "limit_value": getattr(v, "limit_value", None),
            } for v in recent],
            "anomaly_scores": [],
        }

    def apply_operator_decision(self, proposal_id: str, decision: str, operator_id: str, rationale: str = ""):
        proposal = self.proposals.get_proposal(proposal_id)
        decision_l = decision.lower().strip()
        # Every human decision goes to the immutable audit store, not only the in-memory log.
        try:
            self.file_store.write_decision(
                f"{proposal_id}_operator_{int(time.time()*1000)}",
                {"proposal_id": proposal_id, "boundary_id": proposal.boundary_id,
                 "decision": decision_l, "operator_id": operator_id,
                 "rationale": rationale, "timestamp": time.time()},
            )
        except Exception as e:
            self._record_telemetry_error("audit_operator_decision", e)
        if decision_l in ("reject", "rejected"):
            self.authority.record_operator_decision(
                proposal, AuthorizationResult.REJECTED, operator_id, rationale or "rejected by operator"
            )
            return self.proposals.mark_rejected(proposal_id), AuthorizationResult.REJECTED
        if decision_l in ("approve", "approve_loosen", "approve_disable"):
            if proposal.direction == AdaptationDirection.TIGHTEN and decision_l != "approve":
                raise ValueError("Use authorize_proposal for TIGHTEN auto path")
            self.authority.record_operator_decision(
                proposal, AuthorizationResult.OPERATOR_APPROVED, operator_id,
                rationale or f"operator approved {proposal.direction.value}",
            )
            approved = self.proposals.mark_approved(proposal_id)
            return approved, self.apply_approved_proposal(approved)
        raise ValueError(f"Unknown decision: {decision}")

    def get_status(self) -> dict:
        return {
            "boundaries": len(self.boundaries.list_boundaries()),
            "executions": len(self.events.get_all_executions()),
            "violations": len(self.events.get_all_violations()),
            "proposals": len(self.proposals.get_all_proposals()),
            "validations": len(self.validations.get_all_validations()),
            "principles": len(self.principles.list_principles()),
            "telemetry_errors": len(self.telemetry_errors),
        }
