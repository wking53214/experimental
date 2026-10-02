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
