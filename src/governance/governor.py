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
from .proposal import ProposalStore, AdaptationProposal, AdaptationDirection
from .authority import AuthorityModel, AuthorizationResult
from .validation import ValidatorOracle, ValidationStore, ValidationOutcome
from .store import ImmutableFileStore
from .workload import WorkloadClassifier, SmartPatternDetector, ViolationContext
from .metrics import MetricsTracker, EffectivenessOutcome
from .baseline import BaselineComparator


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
        # Core components
        self.principles = PrincipleStore()
        self.boundaries = BoundaryStore()
        self.events = EventStore()
        self.patterns = PatternDetector()
        self.proposals = ProposalStore()
        self.authority = AuthorityModel()
        self.validators = ValidatorOracle()
        self.validations = ValidationStore()

        # Phase 2: Semantic understanding layer
        self.use_semantic = use_semantic
        self.classifier = WorkloadClassifier() if use_semantic else None
        self.smart_patterns = SmartPatternDetector(self.classifier, self.patterns) if use_semantic else None

        # Phase 2 Sprint 2: Metrics tracking
        self.metrics_tracker = MetricsTracker()

        # Phase 2.5: Baseline reasoning (defend against semantic poisoning)
        self.baseline = BaselineComparator()

        # File-based immutable store
        self.file_store = ImmutableFileStore(store_path)

    def execute_against_boundary(
        self,
        boundary_id: str,
        observed_value: Any,
        context: Optional[dict] = None,
    ) -> tuple[ExecutionEvent, Optional[ViolationEvent]]:
        """
        Execute an attempt against a boundary.

        Returns: (execution_event, violation_event or None)
        """
        # Get current boundary
        try:
            boundary = self.boundaries.get_boundary(boundary_id)
        except KeyError:
            raise ValueError(f"Boundary {boundary_id} not found")

        # Record execution
        execution = self.events.record_execution(
            boundary_id=boundary_id,
            boundary_version=boundary.version,
            observed_value=observed_value,
            context=context,
        )

        # Serialize to file
        self.file_store.write_execution_event(
            execution.execution_id,
            {
                "execution_id": execution.execution_id,
                "boundary_id": execution.boundary_id,
                "boundary_version": execution.boundary_version,
                "observed_value": str(observed_value),
                "timestamp": execution.timestamp,
                "context": execution.context,
            }
        )

        # Check for violation
        violation = None
        try:
            is_violation = observed_value > boundary.current_limit
        except TypeError:
            is_violation = False

        if is_violation:
            violation = self.events.record_violation(
                execution_id=execution.execution_id,
                boundary_id=boundary_id,
                boundary_version=boundary.version,
                observed_value=observed_value,
                limit_value=boundary.current_limit,
                context=context,
            )

            # Serialize violation to file
            self.file_store.write_violation_event(
                violation.violation_id,
                {
                    "violation_id": violation.violation_id,
                    "execution_id": violation.execution_id,
                    "boundary_id": violation.boundary_id,
                    "boundary_version": violation.boundary_version,
                    "observed_value": str(violation.observed_value),
                    "limit_value": str(violation.limit_value),
                    "timestamp": violation.timestamp,
                    "context": violation.context,
                }
            )

        return execution, violation

    def detect_and_propose_adaptation(
        self,
        boundary_id: str,
    ) -> Optional[AdaptationProposal]:
        """
        Detect if a pattern has emerged for a boundary.
        If so, propose an adaptation.

        Phase 2: Uses semantic understanding to filter expected violations,
        avoiding false positives on legitimate high-load scenarios.

        Returns: proposal or None
        """
        # Get recent violations
        violations = self.events.get_violations_for_boundary(boundary_id)

        if not violations:
            return None

        # Phase 2: Use smart pattern detection if semantic layer enabled
        if self.use_semantic and self.smart_patterns:
            pattern_detected = self.smart_patterns.detect_pattern(
                boundary_id=boundary_id,
                recent_violations=violations,
            )
        else:
            # Phase 1: Original blind pattern detection
            pattern = self.patterns.detect_pattern(
                boundary_id=boundary_id,
                recent_violations=violations,
            )
            pattern_detected = pattern is not None

        if not pattern_detected:
            return None

        # Pattern detected: propose tightening
        boundary = self.boundaries.get_boundary(boundary_id)

        # Tightening proposal: increase the limit slightly
        # (In real system, this would be more sophisticated)
        new_limit = boundary.current_limit * 0.9  # Tighten by 10%

        # Phase 2: Count expected vs anomalous violations for reasoning
        if self.use_semantic and self.classifier:
            expected_count = sum(
                1 for v in violations
                if self.classifier.is_expected_violation(v)
            )
            anomalous_count = len(violations) - expected_count
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

        # Serialize proposal to file
        self.file_store.write_proposal(
            proposal.proposal_id,
            {
                "proposal_id": proposal.proposal_id,
                "boundary_id": proposal.boundary_id,
                "current_value": str(proposal.current_value),
                "proposed_value": str(proposal.proposed_value),
                "reason": proposal.reason,
                "direction": proposal.direction.value,
                "status": proposal.status.value,
                "created_at": proposal.created_at,
            }
        )

        return proposal

    def authorize_proposal(
        self,
        proposal: AdaptationProposal,
    ) -> tuple[AdaptationProposal, AuthorizationResult]:
        """
        Authorize a proposal based on its direction.

        This is the critical authority control point.
        """
        result = self.authority.authorize_proposal(proposal)

        # Serialize decision
        decision = self.authority.decisions[-1]
        self.file_store.write_decision(
            f"{proposal.proposal_id}_{int(time.time()*1000)}",
            {
                "proposal_id": decision.proposal_id,
                "direction": decision.direction.value,
                "result": decision.result.value,
                "reason": decision.reason,
                "timestamp": decision.timestamp,
            }
        )

        # If auto-approved, mark it
        if result == AuthorizationResult.AUTO_APPROVED:
            approved_proposal = self.proposals.mark_approved(proposal.proposal_id)
            return approved_proposal, result
        else:
            return proposal, result

    def apply_approved_proposal(
        self,
        proposal: AdaptationProposal,
    ) -> BoundaryVersion:
        """
        Apply an approved proposal to update the boundary.
        """
        if proposal.status.value != "approved":
            raise ValueError(
                f"Proposal {proposal.proposal_id} is not approved. "
                f"Status: {proposal.status.value}"
            )

        # Update boundary
        new_version = self.boundaries.update_boundary(
            boundary_id=proposal.boundary_id,
            new_limit=proposal.proposed_value,
        )

        # Mark proposal as applied
        self.proposals.mark_applied(proposal.proposal_id)

        return new_version

    def validate_adaptation(
        self,
        proposal: AdaptationProposal,
        observed_state: Any,
    ) -> ValidationOutcome:
        """
        Validate an adaptation after applying it.
        """
        outcome = self.validators.validate(
            proposal.boundary_id,
            observed_state,
        )

        # Record validation
        self.validations.record_validation(
            proposal_id=proposal.proposal_id,
            boundary_id=proposal.boundary_id,
            expected_condition=f"Violations reduced with new limit {proposal.proposed_value}",
            observed_condition=f"System state: {observed_state}",
            outcome=outcome,
        )

        # Serialize validation
        validation_results = self.validations.get_validations_for_proposal(
            proposal.proposal_id
        )
        if validation_results:
            v = validation_results[0]
            self.file_store.write_validation(
                v.validation_id,
                {
                    "validation_id": v.validation_id,
                    "proposal_id": v.proposal_id,
                    "boundary_id": v.boundary_id,
                    "outcome": v.outcome.value,
                    "expected_condition": v.expected_condition,
                    "observed_condition": v.observed_condition,
                    "timestamp": v.timestamp,
                }
            )

        return outcome

    def verify_governance_integrity(self) -> bool:
        """
        Verify that core governance invariants are maintained.

        This is a critical safety check.
        """
        checks = [
            ("Principles immutable", self.principles.verify_principle_integrity()),
            ("No auto-loosen", self.authority.verify_no_auto_loosen()),
            ("No auto-disable", self.authority.verify_no_auto_disable()),
            ("File store immutability", self.file_store.verify_immutability()),
        ]

        all_pass = all(result for _, result in checks)

        return all_pass, checks

    def get_status(self) -> dict:
        """Get current system status."""
        return {
            "boundaries": len(self.boundaries.list_boundaries()),
            "executions": len(self.events.get_all_executions()),
            "violations": len(self.events.get_all_violations()),
            "proposals": len(self.proposals.get_all_proposals()),
            "validations": len(self.validations.get_all_validations()),
            "principles": len(self.principles.list_principles()),
        }
