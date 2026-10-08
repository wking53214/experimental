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
from .audit_log import AuditLog, AuditIntegrityError
from .authority import AuthorityModel, AuthorizationResult
from .operators import OperatorRegistry
from .grant import is_loosening
from .validation import ValidatorOracle, ValidationStore, ValidationOutcome
from .store import ImmutableFileStore
from .workload import WorkloadClassifier, SmartPatternDetector
from .metrics import MetricsTracker, DetectorPipeline
from .baseline import BaselineComparator
from .anomaly_detector import AdaptiveAnomalyDetector
from .phase9_integration import HybridDetectorPipeline, GenerativePipeline
from .rollback import RollbackExecutor, RollbackReason


class Governor:
    # Theory (white paper sec. 4): automatic tightening stops after this many without a
    # human decision, and violations are interpreted before they can tighten anything.
    DEFAULT_MAX_AUTO_TIGHTENINGS = 3

    def __init__(self, store_path: str = "/tmp/governance_events", use_semantic: bool = True,
                 require_fresh_evidence: bool = False, tighten_cooldown_s: float = 0.0,
                 max_auto_tightenings: Optional[int] = DEFAULT_MAX_AUTO_TIGHTENINGS, clock=time.time,
                 detection: str = "generative", operators: Optional[OperatorRegistry] = None,
                 audit_path: Optional[str] = None, baseline_check_at: Optional[int] = None,
                 strict_patterns: bool = False, evidence_window: Optional[int] = None,
                 audit_anchor: Optional[dict] = None):
        """Limits on automatic tightening (see docs/THREAT_MODEL.md).

        use_semantic and max_auto_tightenings are on by default, per the theory document.
        require_fresh_evidence and tighten_cooldown_s stay off by default. Pass None to
        max_auto_tightenings or False to use_semantic to turn those off.
        detection: 'generative' (default) or 'hybrid'; see docs/BASELINE_COMPARISON.md.
        operators: an OperatorRegistry. When given, operator decisions need a credential that
            authenticates for the operator_id (docs/THREAT_MODEL.md, T9).
        audit_path: file for the hash-chained audit log (docs/THREAT_MODEL.md, T8); in memory if None.
            Setting it also makes limits DURABLE (T13): boundaries, version history, the breaker count
            and the decision list are rebuilt from the log at startup. The log is verified first and
            the governor refuses to start (AuditIntegrityError) if it is broken or if replaying it
            would raise a limit without a recorded grant. Limits must be JSON values (numbers).
            Not restored: pending proposals, unused grants, registered expected-load patterns.
        audit_anchor: a value from audit_anchor() stored earlier; startup fails if the log's history
            no longer matches it (catches truncation and full rewrites).
        evidence_window: only violations among the boundary's most recent N executions count toward
            a tightening. Without it evidence never expires, so a few false alarms spread over a long
            time add up to a tightening (on clean 30-metric data: 3 tightenings in 1500 steps).
        strict_patterns: only patterns registered through register_expected_pattern() (an
            authenticated, audited operator action) excuse violations; a pattern registered
            directly on the classifier is kept but ignored (docs/THREAT_MODEL.md, T4). Off by
            default, which leaves the classifier open to anyone who can call it.
        baseline_check_at: with detection='generative', check each boundary's first N observations
            for contamination once (docs/BASELINE_POISONING.md); the result goes to the audit log and,
            if suspicious, to `baseline_reviews` for a person. Off by default.

        require_fresh_evidence: a new tightening needs a pattern in violations recorded since
            the last change to that boundary, not just the old ones again.
        tighten_cooldown_s: minimum seconds between automatic tightenings of one boundary.
        max_auto_tightenings: at most this many automatic tightenings per boundary until an
            operator calls acknowledge_tightening(); further ones are held, not applied.
        """
        if detection not in ("hybrid", "generative"):
            raise ValueError("detection must be 'hybrid' or 'generative'")
        # 'generative' avoids the hybrid's traditional layer, which alarms on nearly every step of
        # real many-metric telemetry (docs/BASELINE_COMPARISON.md)
        self.detection = detection
        self.require_fresh_evidence = require_fresh_evidence
        self.tighten_cooldown_s = tighten_cooldown_s
        self.max_auto_tightenings = max_auto_tightenings
        self.clock = clock
        self._evidence_cursor: dict = {}
        self._last_auto_tighten_at: dict = {}
        self._auto_tightenings: dict = {}
        self.tightening_holds: list = []
        self._hold_open: set = set()  # boundaries with an unreported hold in progress
        self.baseline_check_at = baseline_check_at
        self.evidence_window = evidence_window
        self._step: dict = {}            # observations seen per boundary
        self._violation_step: dict = {}  # violation_id -> the observation it came from
        self.baseline_reviews: list = []
        self._baseline_logged: set = set()
        self.principles = PrincipleStore()
        self.boundaries = BoundaryStore()
        self.events = EventStore()
        self.patterns = PatternDetector()
        self.proposals = ProposalStore()
        self.audit = AuditLog(audit_path)
        self.authority = AuthorityModel(operators=operators, audit=self.audit)
        self.validators = ValidatorOracle()
        self.validations = ValidationStore()
        self.use_semantic = use_semantic
        self.classifier = WorkloadClassifier() if use_semantic else None
        self._approved_patterns: set = set()
        if strict_patterns and self.classifier is not None:
            self.classifier.approval_gate = lambda p: p.pattern_id in self._approved_patterns
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
        self.restored: Optional[dict] = None
        if self.audit.entries:
            self._restore(audit_anchor)
        elif audit_anchor is not None and audit_anchor.get("length"):
            raise AuditIntegrityError("an anchor was given but the audit log is empty")
        self.boundaries.on_event = self._log_boundary_version

    def _log_boundary_version(self, kind: str, payload: dict) -> None:
        # Write-ahead: the store calls this before the version takes effect. If the write fails the
        # change does not happen.
        self.audit.append(kind, payload)

    def _restore(self, anchor: Optional[dict]) -> None:
        """Rebuild durable state from the audit log. The log is verified first; any replayed step that
        would raise a limit without a recorded grant aborts startup."""
        from .authority import AuthorizationDecision
        from .proposal import AdaptationDirection
        ok, problem = self.audit.verify(anchor)
        if not ok:
            raise AuditIntegrityError(f"audit log failed verification at startup: {problem}")
        versions = 0
        try:
            for e in self.audit.entries:
                p, kind = e["payload"], e["kind"]
                if kind == "boundary_version":
                    self.boundaries.restore_version(p["boundary_id"], p["version"], p["resource"],
                                                    p["limit"], p.get("grant_id"), e["ts"])
                    versions += 1
                elif kind == "authority_decision":
                    eff = p.get("effective_direction")
                    self.authority.decisions.append(AuthorizationDecision(
                        proposal_id=p["proposal_id"], direction=AdaptationDirection(p["direction"]),
                        result=AuthorizationResult(p["result"]), reason=p.get("reason", ""),
                        timestamp=e["ts"], decided_by=p["decided_by"],
                        effective_direction=AdaptationDirection(eff) if eff else None,
                        grant_id=p.get("grant_id"), identity_verified=p.get("identity_verified", False)))
                elif kind == "boundary_update" and p.get("decision") == AuthorizationResult.AUTO_APPROVED.value:
                    b = p["boundary_id"]
                    self._auto_tightenings[b] = self._auto_tightenings.get(b, 0) + 1
                    self._last_auto_tighten_at[b] = e["ts"]
                elif kind == "tightening_acknowledged":
                    self._auto_tightenings[e["payload"]["boundary_id"]] = 0
        except (ValueError, PermissionError, KeyError) as ex:
            raise AuditIntegrityError(f"replaying the audit log failed: {ex}") from ex
        if self.boundaries.unauthorized_loosenings():
            raise AuditIntegrityError("replayed history contains an unauthorized loosening")
        self.restored = {"entries": len(self.audit.entries), "boundaries": len(self.boundaries.boundaries),
                         "versions": versions, "truncated_tail_repaired": self.audit.truncated_tail}
        self.audit.append("restored", dict(self.restored))

    def ensure_boundary(self, boundary_id: str, resource_or_action: str, configured_limit):
        """The start-up call for durable deployments: create the boundary if it does not exist,
        otherwise keep the restored limit. A configured limit higher than the restored one is
        IGNORED (and noted in the audit log), because applying it would loosen without a grant."""
        try:
            current = self.boundaries.get_boundary(boundary_id)
        except KeyError:
            return self.boundaries.create_boundary(boundary_id, resource_or_action, configured_limit)
        if is_loosening(current.current_limit, configured_limit):
            self.audit.append("configured_limit_ignored", {
                "boundary_id": boundary_id, "restored_limit": current.current_limit,
                "configured_limit": configured_limit})
        return current

    def _record_telemetry_error(self, where: str, exc: Exception) -> None:
        self.telemetry_errors.append({
            "where": where, "type": type(exc).__name__, "message": str(exc)[:200],
        })

    def ingest_metrics(self, boundary_id: str, timestamp: float, metrics: dict):
        if boundary_id not in self.detector_pipelines:
            if self.detection == "generative":
                self.detector_pipelines[boundary_id] = GenerativePipeline(
                    boundary_id, baseline_check_at=self.baseline_check_at)
            else:
                self.detector_pipelines[boundary_id] = HybridDetectorPipeline(boundary_id)
        pipeline = self.detector_pipelines[boundary_id]
        step = self._tick(boundary_id)
        pipeline.ingest_metrics(timestamp, metrics)
        report = getattr(pipeline, "baseline_report", None)
        if report is not None and boundary_id not in self._baseline_logged:
            self._baseline_logged.add(boundary_id)
            self.audit.append("baseline_check", {"boundary_id": boundary_id, "suspicious": report["suspicious"],
                                               "flags": report["flags"]})
            if report["suspicious"]:
                self.baseline_reviews.append({"boundary_id": boundary_id, "flags": report["flags"]})
        detection_result = pipeline.detect_anomalies()
        violations = []
        if detection_result.get("anomaly_detected"):
            try:
                boundary = self.boundaries.get_boundary(boundary_id)
                execution = self.events.record_execution(
                    boundary_id=boundary_id, boundary_version=boundary.version,
                    observed_value=detection_result.get("anomaly_score", 0.0),
                    context={"detection_type": f"{self.detection}_multi_metric"},
                )
                violation = self.events.record_violation(
                    execution_id=execution.execution_id, boundary_id=boundary_id,
                    boundary_version=boundary.version,
                    observed_value=detection_result.get("anomaly_score", 0.0),
                    limit_value=0.85, context=detection_result,
                )
                self._violation_step[violation.violation_id] = step
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
        step = self._tick(boundary_id)
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
            self._violation_step[violation.violation_id] = step
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
        if self.require_fresh_evidence:
            violations = violations[self._evidence_cursor.get(boundary_id, 0):]
        if self.evidence_window and violations:
            now = self._step.get(boundary_id, 0)
            violations = [v for v in violations
                          if self._violation_step.get(v.violation_id, now) > now - self.evidence_window]
        if not violations:
            return None
        hold = self._tightening_hold(boundary_id)
        if hold:
            # One record per hold episode. Logging every step while held filled the audit log and
            # this list with thousands of copies of the same fact (7,997 in 8,000 real steps).
            if boundary_id not in self._hold_open:
                self._hold_open.add(boundary_id)
                self.tightening_holds.append({"boundary_id": boundary_id, "reason": hold,
                                              "timestamp": self.clock()})
                self.audit.append("tightening_held", {"boundary_id": boundary_id, "reason": hold})
            return None
        self._hold_open.discard(boundary_id)
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

    def _tick(self, boundary_id: str) -> int:
        self._step[boundary_id] = self._step.get(boundary_id, 0) + 1
        return self._step[boundary_id]

    def _tightening_hold(self, boundary_id: str) -> Optional[str]:
        if (self.max_auto_tightenings is not None
                and self._auto_tightenings.get(boundary_id, 0) >= self.max_auto_tightenings):
            return (f"{self._auto_tightenings[boundary_id]} automatic tightenings since the last "
                    f"operator acknowledgement; a human must review before more")
        last = self._last_auto_tighten_at.get(boundary_id)
        if self.tighten_cooldown_s and last is not None and self.clock() - last < self.tighten_cooldown_s:
            return f"cooldown: {self.tighten_cooldown_s - (self.clock() - last):.0f}s remaining"
        return None

    def acknowledge_tightening(self, boundary_id: str, operator_id: str, credential: Optional[str] = None) -> None:
        """An operator reviewed the automatic tightenings; allow the system to continue."""
        if not operator_id or not str(operator_id).strip():
            raise ValueError("operator_id is required")
        verified = self._authenticate_operator(operator_id, credential)
        self._auto_tightenings[boundary_id] = 0
        self._hold_open.discard(boundary_id)
        self.audit.append("tightening_acknowledged", {
            "boundary_id": boundary_id, "operator_id": operator_id, "identity_verified": verified})

    def register_expected_pattern(self, pattern, operator_id: str, credential: Optional[str] = None,
                                  rationale: str = "") -> None:
        """Register an expected-load pattern as a named operator's decision: authenticated when an
        operator registry exists, always audited. This is the path strict_patterns trusts."""
        if self.classifier is None:
            raise ValueError("semantic classification is off (use_semantic=False)")
        if not operator_id or not str(operator_id).strip():
            raise ValueError("operator_id is required")
        verified = self._authenticate_operator(operator_id, credential)
        self._approved_patterns.add(pattern.pattern_id)
        self.classifier.register_expected_pattern(pattern, trusted=True)
        self.audit.append("pattern_registered", {
            "pattern_id": pattern.pattern_id, "boundary_id": pattern.boundary_id,
            "value_range": list(pattern.expected_value_range), "operator_id": operator_id,
            "identity_verified": verified, "rationale": rationale})

    def _authenticate_operator(self, operator_id: str, credential: Optional[str]) -> bool:
        """True when a registry is configured and the credential authenticates; raises if a
        registry is configured and it does not; False when there is no registry (claim only)."""
        registry = self.authority.operators
        if registry is None:
            return False
        if not registry.authenticate(operator_id, credential):
            self.audit.append("operator_auth_failed", {"operator_id": operator_id})
            raise PermissionError(f"operator {operator_id} could not be authenticated")
        return True

    def authorize_proposal(self, proposal: AdaptationProposal):
        result = self.authority.authorize_proposal(proposal)
        if result == AuthorizationResult.AUTO_APPROVED:
            return self.proposals.mark_approved(proposal.proposal_id), result
        return proposal, result

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
        self.audit.append("boundary_update", {
            "boundary_id": current.boundary_id, "version": new_version.version,
            "new_limit": current.proposed_value, "proposal_id": current.proposal_id,
            "decision": decision.result.value, "grant_id": decision.grant_id})
        if decision.result == AuthorizationResult.AUTO_APPROVED:
            self._auto_tightenings[current.boundary_id] = self._auto_tightenings.get(current.boundary_id, 0) + 1
            self._last_auto_tighten_at[current.boundary_id] = self.clock()
        self.proposals.mark_applied(current.proposal_id)
        self._evidence_cursor[current.boundary_id] = len(
            self.events.get_violations_for_boundary(current.boundary_id))
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

    def review_effectiveness(self, proposal_id: str, observed_state) -> tuple:
        """Validation layer: judge an applied adaptation against an independent signal.

        A DEGRADED outcome rolls the boundary back. A rollback that loosens is queued for a
        human (see RollbackExecutor), so the system still never loosens on its own.
        Returns (outcome, rollback_decision).
        """
        proposal = self.proposals.get_proposal(proposal_id)
        outcome = self.validate_adaptation(proposal, observed_state)
        decision = None
        if outcome == ValidationOutcome.DEGRADED and self.rollback.should_attempt_rollback(
                proposal.boundary_id, "degraded", 1.0):
            decision = self.rollback.execute_rollback(
                proposal_id, proposal.boundary_id, RollbackReason.DEGRADED_METRICS)
        return outcome, decision

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
            ("Audit log hash chain intact", self.audit.verify()[0]),
            ("Decision list matches audit log", self.authority.audit_consistent()),
            ("Every loosening is in the audit log with its grant", self._loosenings_logged()),
        ]
        return all(r for _, r in checks), checks

    def _loosenings_logged(self) -> bool:
        logged = {(e["payload"]["boundary_id"], e["payload"]["version"]): e["payload"]["grant_id"]
                  for e in self.audit.find("boundary_update")}
        return all(logged.get(key) == grant_id
                   for key, grant_id in self.boundaries.authorized_loosenings.items())

    def audit_anchor(self) -> dict:
        """Store this somewhere the governor's process cannot write; verify against it later."""
        return self.audit.anchor()

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
                        context={"source": f"{self.detection}_pipeline"},
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
        return [
            p for p in self.proposals.get_all_proposals()
            if p.status == ProposalStatus.PENDING_REVIEW
            or (p.status == ProposalStatus.PENDING and p.direction.value in ("loosen", "disable"))
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

    def apply_operator_decision(self, proposal_id: str, decision: str, operator_id: str, rationale: str = "",
                                credential: Optional[str] = None):
        proposal = self.proposals.get_proposal(proposal_id)
        self._authenticate_operator(operator_id, credential)  # before anything is recorded
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
                proposal, AuthorizationResult.REJECTED, operator_id, rationale or "rejected by operator",
                credential=credential,
            )
            return self.proposals.mark_rejected(proposal_id), AuthorizationResult.REJECTED
        if decision_l in ("approve", "approve_loosen", "approve_disable"):
            if proposal.direction == AdaptationDirection.TIGHTEN and decision_l != "approve":
                raise ValueError("Use authorize_proposal for TIGHTEN auto path")
            self.authority.record_operator_decision(
                proposal, AuthorizationResult.OPERATOR_APPROVED, operator_id,
                rationale or f"operator approved {proposal.direction.value}",
                credential=credential,
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
