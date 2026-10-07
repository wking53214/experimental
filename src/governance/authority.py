"""
Authority Model — Sole System Gate for Auto-Approval

CONTRACT:
    - TIGHTEN  → system may AUTO_APPROVE
    - LOOSEN   → REQUIRES_HUMAN_REVIEW (system MUST NOT auto-approve)
    - DISABLE  → REQUIRES_HUMAN_REVIEW (system MUST NOT auto-approve)

H2 FIXED: Operator grants use AuthorizationResult.OPERATOR_APPROVED.
verify_no_auto_loosen / verify_no_auto_disable only flag system AUTO_APPROVED.
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import time

from .grant import AuthorizationGrant, _ISSUER_KEY
from .proposal import AdaptationProposal, AdaptationDirection, ProposalStatus, effective_direction


class AuthorizationResult(Enum):
    AUTO_APPROVED = "auto_approved"
    OPERATOR_APPROVED = "operator_approved"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    REJECTED = "rejected"


@dataclass
class AuthorizationDecision:
    proposal_id: str
    direction: AdaptationDirection
    result: AuthorizationResult
    reason: str
    timestamp: float
    decided_by: str = "system"
    effective_direction: Optional[AdaptationDirection] = None
    grant_id: Optional[str] = None


class AuthorityModel:
    def __init__(self):
        self.decisions: list = []
        self.grants: dict = {}  # proposal_id -> AuthorizationGrant (operator approvals only)

    def evaluate_proposal(self, proposal: AdaptationProposal) -> AuthorizationResult:
        """Auto-approve only if BOTH the declared direction and the direction implied by
        the proposal's own values are TIGHTEN. The stricter reading wins, so a proposal
        labelled TIGHTEN whose value is larger than its current value needs a human."""
        implied = effective_direction(proposal.current_value, proposal.proposed_value)
        if proposal.direction == AdaptationDirection.TIGHTEN:
            if implied == AdaptationDirection.TIGHTEN:
                return AuthorizationResult.AUTO_APPROVED
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW
        if proposal.direction in (AdaptationDirection.LOOSEN, AdaptationDirection.DISABLE):
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW
        return AuthorizationResult.REJECTED

    def authorize_proposal(self, proposal: AdaptationProposal, breaker_open: bool = False) -> AuthorizationResult:
        """breaker_open: the caller's circuit breaker has tripped for this boundary, so
        an otherwise automatic tightening is held for a human instead."""
        result = self.evaluate_proposal(proposal)
        if breaker_open and result == AuthorizationResult.AUTO_APPROVED:
            result = AuthorizationResult.REQUIRES_HUMAN_REVIEW
            reason = "circuit breaker open: too many automatic tightenings since the last human decision"
        else:
            reason = {
                AuthorizationResult.AUTO_APPROVED: "TIGHTEN auto-approved by authority model",
                AuthorizationResult.REQUIRES_HUMAN_REVIEW: (
                    f"{proposal.direction.value} requires human review"
                    if proposal.direction != AdaptationDirection.TIGHTEN
                    else "declared TIGHTEN but its values loosen: requires human review"),
                AuthorizationResult.REJECTED: "Proposal rejected by authority model",
            }.get(result, "unknown")
        self.decisions.append(AuthorizationDecision(
            proposal_id=proposal.proposal_id,
            direction=proposal.direction,
            result=result,
            reason=reason,
            timestamp=time.time(),
            decided_by="system",
            effective_direction=effective_direction(proposal.current_value, proposal.proposed_value),
        ))
        return result

    def record_operator_decision(self, proposal, result, operator_id, rationale=""):
        if not operator_id or not str(operator_id).strip():
            raise ValueError("operator_id is required for an operator decision")
        grant = None
        if result == AuthorizationResult.OPERATOR_APPROVED:
            grant = AuthorizationGrant(
                _key=_ISSUER_KEY, proposal_id=proposal.proposal_id,
                boundary_id=proposal.boundary_id, new_limit=proposal.proposed_value,
                operator_id=operator_id)
            self.grants[proposal.proposal_id] = grant
        decision = AuthorizationDecision(
            proposal_id=proposal.proposal_id,
            direction=proposal.direction,
            result=result,
            reason=rationale or f"operator {operator_id} decided {result.value}",
            timestamp=time.time(),
            decided_by=operator_id,
            effective_direction=effective_direction(proposal.current_value, proposal.proposed_value),
            grant_id=grant.grant_id if grant else None,
        )
        self.decisions.append(decision)
        return decision

    def latest_decision(self, proposal_id: str):
        for d in reversed(self.decisions):
            if d.proposal_id == proposal_id:
                return d
        return None

    def verify_no_auto_loosen(self) -> bool:
        for d in self.decisions:
            implied_loosen = d.effective_direction == AdaptationDirection.LOOSEN
            if d.direction == AdaptationDirection.LOOSEN or implied_loosen:
                if d.result == AuthorizationResult.AUTO_APPROVED and d.decided_by == "system":
                    return False
        return True

    def verify_no_auto_disable(self) -> bool:
        for d in self.decisions:
            if d.direction == AdaptationDirection.DISABLE:
                if d.result == AuthorizationResult.AUTO_APPROVED and d.decided_by == "system":
                    return False
        return True

    def get_decisions(self):
        return list(self.decisions)
