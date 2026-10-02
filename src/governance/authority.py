"""
Authority Model: enforces asymmetric adaptive authority.

TIGHTEN  → can be auto-approved
LOOSEN  → human review required (MUST NOT auto-approve)
DISABLE → human review required (MUST NOT auto-approve)

This separation is deliberate and separate from boundary update logic and cannot be bypassed.
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import time

from .proposal import AdaptationProposal, AdaptationDirection, ProposalStatus


class AuthorizationResult(Enum):
    """Result of an authorization check."""
    AUTO_APPROVED = "auto_approved"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    REJECTED = "rejected"


@dataclass
class AuthorizationDecision:
    """Record of an authorization decision."""
    proposal_id: str
    direction: AdaptationDirection
    result: AuthorizationResult
    reason: str
    timestamp: float
    decided_by: str = "system"


class AuthorityModel:
    """
    Enforces authority rules.

    This class contains the ONLY code path that decides whether a proposal
    can be automatically approved. It cannot be bypassed.
    """

    def __init__(self):
        self.decisions: list[AuthorizationDecision] = []

    def evaluate_proposal(self, proposal: AdaptationProposal) -> AuthorizationResult:
        """Evaluate a proposal according to authority rules."""
        if proposal.direction == AdaptationDirection.TIGHTEN:
            return AuthorizationResult.AUTO_APPROVED

        if proposal.direction == AdaptationDirection.LOOSEN:
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW

        if proposal.direction == AdaptationDirection.DISABLE:
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW

        return AuthorizationResult.REJECTED

    def authorize_proposal(self, proposal: AdaptationProposal) -> AuthorizationResult:
        """Authorize a proposal and record the decision."""
        result = self.evaluate_proposal(proposal)
        reason = {
            AuthorizationResult.AUTO_APPROVED: "TIGHTEN auto-approved by authority model",
            AuthorizationResult.REQUIRES_HUMAN_REVIEW: f"{proposal.direction.value} requires human review",
            AuthorizationResult.REJECTED: "Proposal rejected by authority model",
        }.get(result, "unknown")

        decision = AuthorizationDecision(
            proposal_id=proposal.proposal_id,
            direction=proposal.direction,
            result=result,
            reason=reason,
            timestamp=time.time(),
            decided_by="system",
        )
        self.decisions.append(decision)
        return result

    def record_operator_decision(
        self,
        proposal: AdaptationProposal,
        result: AuthorizationResult,
        operator_id: str,
        rationale: str = "",
    ) -> AuthorizationDecision:
        """Record a human operator decision for LOOSEN/DISABLE."""
        decision = AuthorizationDecision(
            proposal_id=proposal.proposal_id,
            direction=proposal.direction,
            result=result,
            reason=rationale or f"operator {operator_id} decided {result.value}",
            timestamp=time.time(),
            decided_by=operator_id,
        )
        self.decisions.append(decision)
        return decision

    def verify_no_auto_loosen(self) -> bool:
        """Verify that LOOSEN operations were never system auto-approved."""
        for decision in self.decisions:
            if decision.direction == AdaptationDirection.LOOSEN:
                if (
                    decision.result == AuthorizationResult.AUTO_APPROVED
                    and decision.decided_by == "system"
                ):
                    return False
        return True

    def verify_no_auto_disable(self) -> bool:
        """Verify that DISABLE operations were never system auto-approved."""
        for decision in self.decisions:
            if decision.direction == AdaptationDirection.DISABLE:
                if (
                    decision.result == AuthorizationResult.AUTO_APPROVED
                    and decision.decided_by == "system"
                ):
                    return False
        return True

    def get_decisions(self) -> list:
        return list(self.decisions)
