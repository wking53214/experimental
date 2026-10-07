"""
Automatic rollback and recovery mechanisms.

Phase 2 Sprint 3: When adaptations degrade outcomes, automatically revert
to previous boundary version. Prevent rollback loops and escalate persistent failures.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class RollbackReason(Enum):
    """Why a rollback occurred."""
    DEGRADED_METRICS = "degraded_metrics"
    SLO_VIOLATION = "slo_violation"
    ERROR_RATE_INCREASE = "error_rate_increase"
    MANUAL_REQUEST = "manual_request"
    POLICY_VIOLATION = "policy_violation"


@dataclass
class RollbackDecision:
    """Record of a rollback decision."""
    rollback_id: str
    proposal_id: str
    boundary_id: str
    previous_version: int
    reverted_to_version: int
    reason: RollbackReason
    metrics_before_adaptation: dict = field(default_factory=dict)
    metrics_after_adaptation: dict = field(default_factory=dict)
    metrics_after_rollback: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    notes: dict = field(default_factory=dict)


@dataclass
class RollbackPreventionRule:
    """Rules to prevent unnecessary rollbacks."""
    boundary_id: str
    max_rollbacks_per_hour: int = 3
    min_time_between_rollbacks_seconds: int = 300
    degradation_threshold: float = 0.10  # 10% degradation triggers rollback


class RollbackManager:
    """
    Manages automatic rollback decisions and execution.

    Detects degraded outcomes and reverts adaptations.
    Prevents rollback loops and escalates persistent issues.
    """

    def __init__(self):
        self.rollback_history: dict[str, RollbackDecision] = {}
        self.prevention_rules: dict[str, RollbackPreventionRule] = {}
        self.rollback_sequence = []  # Ordered list of rollback IDs

    def register_prevention_rule(
        self,
        boundary_id: str,
        max_rollbacks_per_hour: int = 3,
        min_time_between_rollbacks_seconds: int = 300,
    ) -> RollbackPreventionRule:
        """Register rollback prevention rules for a boundary."""
        rule = RollbackPreventionRule(
            boundary_id=boundary_id,
            max_rollbacks_per_hour=max_rollbacks_per_hour,
            min_time_between_rollbacks_seconds=min_time_between_rollbacks_seconds,
        )
        self.prevention_rules[boundary_id] = rule
        return rule

    def should_rollback(
        self,
        boundary_id: str,
        effectiveness_outcome: str,
        confidence: float,
    ) -> bool:
        """
        Determine if rollback should occur.

        Criteria:
        - Effectiveness outcome is DEGRADED
        - Confidence is high (>0.7)
        - Not too recent (cooldown period passed)
        - Haven't exceeded rollback limits
        """
        # Only rollback on degraded outcomes with confidence
        if effectiveness_outcome != "degraded":
            return False

        if confidence < 0.7:
            return False

        # Check prevention rules
        if boundary_id in self.prevention_rules:
            if not self._check_prevention_rules(boundary_id):
                return False

        return True

    def _check_prevention_rules(self, boundary_id: str) -> bool:
        """Check if rollback would violate prevention rules."""
        rule = self.prevention_rules[boundary_id]
        now = time.time()

        # Get recent rollbacks for this boundary
        recent_rollbacks = [
            rb for rb in self.rollback_history.values()
            if rb.boundary_id == boundary_id and
            (now - rb.timestamp) < 3600  # Last hour
        ]

        # Check max rollbacks per hour
        if len(recent_rollbacks) >= rule.max_rollbacks_per_hour:
            return False

        # Check minimum time between rollbacks
        if recent_rollbacks:
            last_rollback = max(recent_rollbacks, key=lambda r: r.timestamp)
            time_since_last = now - last_rollback.timestamp
            if time_since_last < rule.min_time_between_rollbacks_seconds:
                return False

        return True

    def create_rollback_decision(
        self,
        rollback_id: str,
        proposal_id: str,
        boundary_id: str,
        previous_version: int,
        reverted_to_version: int,
        reason: RollbackReason,
        metrics_before: Optional[dict] = None,
        metrics_after: Optional[dict] = None,
        notes: Optional[dict] = None,
    ) -> RollbackDecision:
        """Create a rollback decision record."""
        decision = RollbackDecision(
            rollback_id=rollback_id,
            proposal_id=proposal_id,
            boundary_id=boundary_id,
            previous_version=previous_version,
            reverted_to_version=reverted_to_version,
            reason=reason,
            metrics_before_adaptation=metrics_before or {},
            metrics_after_adaptation=metrics_after or {},
            notes=notes or {},
        )

        self.rollback_history[rollback_id] = decision
        self.rollback_sequence.append(rollback_id)

        return decision

    def record_post_rollback_metrics(
        self,
        rollback_id: str,
        metrics: dict,
    ) -> None:
        """Record metrics after rollback to verify recovery."""
        if rollback_id in self.rollback_history:
            self.rollback_history[rollback_id].metrics_after_rollback = metrics

    def get_rollback_by_proposal(self, proposal_id: str) -> Optional[RollbackDecision]:
        """Get rollback decision for a proposal."""
        for rollback in self.rollback_history.values():
            if rollback.proposal_id == proposal_id:
                return rollback
        return None

    def get_boundary_rollback_summary(self, boundary_id: str) -> dict:
        """Get rollback summary for a boundary."""
        boundary_rollbacks = [
            rb for rb in self.rollback_history.values()
            if rb.boundary_id == boundary_id
        ]

        if not boundary_rollbacks:
            return {
                "total": 0,
                "by_reason": {},
                "last_rollback": None,
            }

        by_reason = {}
        for rollback in boundary_rollbacks:
            reason = rollback.reason.value
            by_reason[reason] = by_reason.get(reason, 0) + 1

        last_rollback = max(boundary_rollbacks, key=lambda r: r.timestamp)

        return {
            "total": len(boundary_rollbacks),
            "by_reason": by_reason,
            "last_rollback": {
                "rollback_id": last_rollback.rollback_id,
                "timestamp": last_rollback.timestamp,
                "reason": last_rollback.reason.value,
            },
        }

    def is_boundary_escalated(self, boundary_id: str) -> bool:
        """
        Check if boundary has too many rollbacks (escalate to human).

        Escalation triggers if:
        - More than 5 rollbacks in the last hour
        - More than 3 consecutive rollbacks
        """
        rollbacks = [
            rb for rb in self.rollback_history.values()
            if rb.boundary_id == boundary_id
        ]

        if not rollbacks:
            return False

        # Check rollbacks in last hour
        now = time.time()
        recent = [r for r in rollbacks if (now - r.timestamp) < 3600]
        if len(recent) > 5:
            return True

        # Check consecutive rollbacks (3 in a row within 5 minutes)
        consecutive = 1
        sorted_rollbacks = sorted(rollbacks, key=lambda r: r.timestamp)
        for i in range(1, len(sorted_rollbacks)):
            time_diff = sorted_rollbacks[i].timestamp - sorted_rollbacks[i-1].timestamp
            if time_diff < 300:  # 5 minutes
                consecutive += 1
                if consecutive >= 3:
                    return True
            else:
                consecutive = 1

        return False


class RollbackExecutor:
    """
    Executes rollback operations.

    Reverts boundary to previous version.
    """

    def __init__(self, governor):
        """Initialize with reference to governor."""
        self.governor = governor
        self.rollback_manager = RollbackManager()
        # Proposal ids of rollbacks that would raise a limit and are waiting for a human.
        self.queued_for_review: list = []

    def execute_rollback(
        self,
        proposal_id: str,
        boundary_id: str,
        reason: RollbackReason,
        operator_id: Optional[str] = None,
        credential: Optional[str] = None,
    ) -> Optional[RollbackDecision]:
        """
        Roll a boundary back to its previous limit.

        Going back to a looser limit is a loosening, so it goes through the same gate as
        any other: without `operator_id` it is queued for human review (see
        `queued_for_review`) and nothing changes (returns None). With an `operator_id`
        it is recorded as that operator's decision and applied. Going back to a tighter
        limit is a tightening and follows the normal automatic path.

        Returns the rollback decision if a rollback was executed, None otherwise.
        """
        from .grant import is_loosening
        from .proposal import AdaptationDirection

        current_boundary = self.governor.boundaries.get_boundary(boundary_id)
        history = self.governor.boundaries.get_boundary_history(boundary_id)

        if current_boundary.version <= 1:
            # No previous version to rollback to
            return None

        previous_version = history.get_version(current_boundary.version - 1)
        target = previous_version.current_limit
        loosening = is_loosening(current_boundary.current_limit, target)

        proposal = self.governor.proposals.create_proposal(
            boundary_id=boundary_id,
            source_evidence=[proposal_id],
            current_value=current_boundary.current_limit,
            proposed_value=target,
            reason=f"rollback of {proposal_id}: {reason.value}",
            direction=AdaptationDirection.LOOSEN if loosening else AdaptationDirection.TIGHTEN,
        )
        if loosening:
            self.governor.submit_for_review(proposal)
            if operator_id is None:
                self.queued_for_review.append(proposal.proposal_id)
                return None
            _, reverted_boundary = self.governor.apply_operator_decision(
                proposal.proposal_id, "approve_loosen", operator_id,
                f"operator-initiated rollback ({reason.value})", credential=credential,
            )
        else:
            approved, _ = self.governor.authorize_proposal(proposal)
            reverted_boundary = self.governor.apply_approved_proposal(approved)

        # Create rollback decision
        import uuid
        rollback_id = str(uuid.uuid4())

        decision = self.rollback_manager.create_rollback_decision(
            rollback_id=rollback_id,
            proposal_id=proposal_id,
            boundary_id=boundary_id,
            previous_version=current_boundary.version,
            reverted_to_version=reverted_boundary.version,
            reason=reason,
            notes={
                "previous_limit": previous_version.current_limit,
                "reverted_limit": reverted_boundary.current_limit,
                "initiated_by": operator_id or "system",
            },
        )

        return decision

    def should_attempt_rollback(
        self,
        boundary_id: str,
        effectiveness_outcome: str,
        confidence: float,
    ) -> bool:
        """Check if rollback should be attempted."""
        return self.rollback_manager.should_rollback(
            boundary_id,
            effectiveness_outcome,
            confidence,
        )
