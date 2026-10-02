"""
Adaptation proposals.

A proposal is an explicit, separate object from boundary change.
It identifies what should change, why, and in what direction.
The authority model then decides whether to approve it.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time
import uuid


class AdaptationDirection(Enum):
    """Direction of adaptation."""
    TIGHTEN = "tighten"
    LOOSEN = "loosen"
    DISABLE = "disable"


class ProposalStatus(Enum):
    """Status of a proposal."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


@dataclass
class AdaptationProposal:
    """
    An explicit proposal to modify a boundary.

    This is separate from application. The proposal exists first,
    then authority decides whether to approve it.
    """
    proposal_id: str
    boundary_id: str
    source_evidence: list[str]
    current_value: Any
    proposed_value: Any
    reason: str
    direction: AdaptationDirection
    status: ProposalStatus
    created_at: float
    approved_at: Optional[float] = None
    applied_at: Optional[float] = None
    notes: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.proposal_id)


class ProposalStore:
    """
    Manages adaptation proposals.

    Key: proposals are immutable once created. Status changes are tracked,
    but the proposal itself does not mutate.
    """

    def __init__(self):
        self.proposals: dict[str, AdaptationProposal] = {}
        self._pending = []
        self._approved = []
        self._rejected = []
        self._applied = []

    def create_proposal(
        self,
        boundary_id: str,
        source_evidence: list[str],
        current_value: Any,
        proposed_value: Any,
        reason: str,
        direction: AdaptationDirection,
    ) -> AdaptationProposal:
        """Create a new adaptation proposal."""
        proposal = AdaptationProposal(
            proposal_id=str(uuid.uuid4()),
            boundary_id=boundary_id,
            source_evidence=source_evidence,
            current_value=current_value,
            proposed_value=proposed_value,
            reason=reason,
            direction=direction,
            status=ProposalStatus.PENDING,
            created_at=time.time(),
        )
        self.proposals[proposal.proposal_id] = proposal
        self._pending.append(proposal.proposal_id)
        return proposal

    def get_proposal(self, proposal_id: str) -> AdaptationProposal:
        """Retrieve a proposal."""
        if proposal_id not in self.proposals:
            raise KeyError(f"Proposal {proposal_id} not found")
        return self.proposals[proposal_id]

    def mark_approved(self, proposal_id: str) -> AdaptationProposal:
        """Mark a proposal as approved."""
        proposal = self.get_proposal(proposal_id)
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(
                f"Proposal {proposal_id} is not pending. "
                f"Current status: {proposal.status.value}"
            )

        # Create a new proposal object with updated status
        # (We cannot mutate the original)
        updated = AdaptationProposal(
            proposal_id=proposal.proposal_id,
            boundary_id=proposal.boundary_id,
            source_evidence=proposal.source_evidence,
            current_value=proposal.current_value,
            proposed_value=proposal.proposed_value,
            reason=proposal.reason,
            direction=proposal.direction,
            status=ProposalStatus.APPROVED,
            created_at=proposal.created_at,
            approved_at=time.time(),
            notes=proposal.notes,
        )
        self.proposals[proposal_id] = updated

        if proposal_id in self._pending:
            self._pending.remove(proposal_id)
        self._approved.append(proposal_id)

        return updated

    def mark_rejected(self, proposal_id: str) -> AdaptationProposal:
        """Mark a proposal as rejected."""
        proposal = self.get_proposal(proposal_id)
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(
                f"Proposal {proposal_id} is not pending. "
                f"Current status: {proposal.status.value}"
            )

        updated = AdaptationProposal(
            proposal_id=proposal.proposal_id,
            boundary_id=proposal.boundary_id,
            source_evidence=proposal.source_evidence,
            current_value=proposal.current_value,
            proposed_value=proposal.proposed_value,
            reason=proposal.reason,
            direction=proposal.direction,
            status=ProposalStatus.REJECTED,
            created_at=proposal.created_at,
            notes=proposal.notes,
        )
        self.proposals[proposal_id] = updated

        if proposal_id in self._pending:
            self._pending.remove(proposal_id)
        self._rejected.append(proposal_id)

        return updated

    def mark_applied(self, proposal_id: str) -> AdaptationProposal:
        """Mark a proposal as applied."""
        proposal = self.get_proposal(proposal_id)
        if proposal.status != ProposalStatus.APPROVED:
            raise ValueError(
                f"Proposal {proposal_id} is not approved. "
                f"Current status: {proposal.status.value}"
            )

        updated = AdaptationProposal(
            proposal_id=proposal.proposal_id,
            boundary_id=proposal.boundary_id,
            source_evidence=proposal.source_evidence,
            current_value=proposal.current_value,
            proposed_value=proposal.proposed_value,
            reason=proposal.reason,
            direction=proposal.direction,
            status=ProposalStatus.APPLIED,
            created_at=proposal.created_at,
            applied_at=time.time(),
            notes=proposal.notes,
        )
        self.proposals[proposal_id] = updated

        if proposal_id in self._approved:
            self._approved.remove(proposal_id)
        self._applied.append(proposal_id)

        return updated

    def get_pending_proposals(self) -> list[AdaptationProposal]:
        """Get all pending proposals."""
        return [self.proposals[pid] for pid in self._pending]

    def get_all_proposals(self) -> list[AdaptationProposal]:
        """Get all proposals."""
        return list(self.proposals.values())


class ProposalGenerator:
    """
    Phase 8D: Generate governance proposals from detector pipeline output.

    Converts anomaly detection results into adaptation proposals:
    - Multi-metric anomalies → TIGHTEN boundary
    - Pareto gaming → ADJUST metric emphasis
    - Sustained anomalies → DISABLE boundary (after N occurrences)

    Implements decision logic for proposal direction and severity.
    """

    def __init__(self,
                 boundary_id: str,
                 current_threshold: float = 0.05,
                 tighten_factor: float = 0.9,
                 loosen_factor: float = 1.1):
        """
        Initialize proposal generator for a boundary.

        Args:
            boundary_id: The boundary being adapted
            current_threshold: Current boundary threshold value
            tighten_factor: Multiplier for tightening proposals (< 1.0)
            loosen_factor: Multiplier for loosening proposals (> 1.0)
        """
        self.boundary_id = boundary_id
        self.current_threshold = current_threshold
        self.tighten_factor = tighten_factor
        self.loosen_factor = loosen_factor

        # Tracking
        self.anomaly_history = []  # Recent anomalies
        self.proposal_count = 0

    def generate_proposal(self,
                         detection_result: dict,
                         evidence_ids: list[str]) -> Optional[AdaptationProposal]:
        """
        Generate a proposal based on detector pipeline output.

        Args:
            detection_result: Output from DetectorPipeline.detect_anomalies()
            evidence_ids: IDs of violation/execution events that triggered this

        Returns:
            AdaptationProposal if anomaly warrants action, None otherwise
        """
        # Can generate proposal if either anomaly OR gaming detected
        anomaly_detected = detection_result.get("anomaly_detected", False)
        gaming_detected = detection_result.get("gaming_detected", False)

        if not (anomaly_detected or gaming_detected):
            return None

        anomaly_score = detection_result.get("anomaly_score", 0.0)
        anomaly_count = detection_result.get("anomaly_count", 0)
        explanation = detection_result.get("explanation", "Unknown anomaly")

        # Track anomaly
        self.anomaly_history.append({
            "timestamp": time.time(),
            "score": anomaly_score,
            "gaming": gaming_detected,
            "count": anomaly_count,
        })

        # Keep last 10 anomalies
        if len(self.anomaly_history) > 10:
            self.anomaly_history = self.anomaly_history[-10:]

        # Get gaming score if available
        gaming_score = detection_result.get("gaming_score", 0.0)

        # Decide on proposal
        if gaming_detected and gaming_score > 0.7:
            # Pareto gaming with high confidence: tighten significantly
            direction = AdaptationDirection.TIGHTEN
            severity = 0.95
            reason = f"Pareto gaming detected: {explanation}"
        elif anomaly_count >= 3:
            # Multiple metrics anomalous: tighten moderately
            direction = AdaptationDirection.TIGHTEN
            severity = 0.75
            reason = f"Multi-metric anomaly ({anomaly_count} metrics): {explanation}"
        elif anomaly_score > 0.85:
            # High confidence anomaly: tighten
            direction = AdaptationDirection.TIGHTEN
            severity = 0.60
            reason = f"High-confidence anomaly detected: {explanation}"
        else:
            # Low confidence: don't propose
            return None

        # Check for repeated anomalies (sustained pattern)
        recent_anomalies = len(self.anomaly_history)
        if recent_anomalies >= 3:
            # 3+ anomalies in recent history: increase severity
            severity = min(1.0, severity + 0.2)

        # Calculate proposed value
        if direction == AdaptationDirection.TIGHTEN:
            proposed_value = self.current_threshold * self.tighten_factor ** severity
        else:
            proposed_value = self.current_threshold * self.loosen_factor ** severity

        # Create proposal
        self.proposal_count += 1
        proposal = AdaptationProposal(
            proposal_id=f"prop_{self.boundary_id}_{int(time.time() * 1000)}_{self.proposal_count}",
            boundary_id=self.boundary_id,
            source_evidence=evidence_ids,
            current_value=self.current_threshold,
            proposed_value=proposed_value,
            reason=reason,
            direction=direction,
            status=ProposalStatus.PENDING,
            created_at=time.time(),
            notes={
                "anomaly_score": anomaly_score,
                "gaming_detected": gaming_detected,
                "anomaly_count": anomaly_count,
                "severity": severity,
                "recent_anomalies": recent_anomalies,
            }
        )

        return proposal

    def get_sustained_pattern(self) -> Optional[dict]:
        """
        Check if recent anomalies form a sustained pattern.

        Returns dict with pattern analysis or None if no pattern.
        """
        if len(self.anomaly_history) < 3:
            return None

        recent = self.anomaly_history[-5:]
        avg_score = sum(a["score"] for a in recent) / len(recent)
        gaming_count = sum(1 for a in recent if a["gaming"])
        avg_anomalies = sum(a["count"] for a in recent) / len(recent)

        if avg_score > 0.7 or gaming_count >= 2:
            return {
                "pattern": "sustained_anomalies",
                "recent_count": len(recent),
                "avg_score": avg_score,
                "gaming_incidents": gaming_count,
                "avg_anomalous_metrics": avg_anomalies,
                "severity": min(1.0, avg_score * 1.2),
            }

        return None

    def reset_history(self) -> None:
        """Reset anomaly history (e.g., after proposal accepted)."""
        self.anomaly_history = []
