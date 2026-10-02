"""
Authority enforcement.

This is the critical control point.

TIGHTEN → automatic approval permitted
LOOSEN  → human review required (MUST NOT auto-approve)
DISABLE → human review required (MUST NOT auto-approve)

This logic is separate from boundary update logic and cannot be bypassed.
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
        """
        Evaluate a proposal according to authority rules.

        CRITICAL: This is the only place that auto-approval can be granted.
        """

        # TIGHTEN: can be auto-approved
        if proposal.direction == AdaptationDirection.TIGHTEN:
            return AuthorizationResult.AUTO_APPROVED

        # LOOSEN: MUST require human review (cannot auto-approve)
        if proposal.direction == AdaptationDirection.LOOSEN:
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW

        # DISABLE: MUST require human review (cannot auto-approve)
        if proposal.direction == AdaptationDirection.DISABLE:
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW

        # Unknown direction: reject
        return AuthorizationResult.REJECTED

    def authorize_proposal(
        self,
        proposal: AdaptationProposal,
    ) -> AuthorizationResult:
        """
        Authorize a proposal based on direction.

        Records the decision for audit.
        """
        result = self.evaluate_proposal(proposal)

        reason = ""
        if result == AuthorizationResult.AUTO_APPROVED:
            reason = f"TIGHTEN direction allows automatic approval"
        elif result == AuthorizationResult.REQUIRES_HUMAN_REVIEW:
            reason = f"{proposal.direction.value} direction requires human review"
        else:
            reason = f"Unknown direction: {proposal.direction}"

        decision = AuthorizationDecision(
            proposal_id=proposal.proposal_id,
            direction=proposal.direction,
            result=result,
            reason=reason,
            timestamp=time.time(),
        )
        self.decisions.append(decision)

        return result

    def can_auto_approve(self, proposal: AdaptationProposal) -> bool:
        """
        Check if a proposal can be automatically approved.

        This is the only question that matters.
        """
        return self.evaluate_proposal(proposal) == AuthorizationResult.AUTO_APPROVED

    def get_decisions(self) -> list[AuthorizationDecision]:
        """Get all authorization decisions."""
        return list(self.decisions)

    def verify_no_auto_loosen(self) -> bool:
        """
        Verify that LOOSEN operations were never auto-approved.

        This is a critical invariant check.
        """
        for decision in self.decisions:
            if decision.direction == AdaptationDirection.LOOSEN:
                if decision.result == AuthorizationResult.AUTO_APPROVED:
                    return False
        return True

    def verify_no_auto_disable(self) -> bool:
        """
        Verify that DISABLE operations were never auto-approved.

        This is a critical invariant check.
        """
        for decision in self.decisions:
            if decision.direction == AdaptationDirection.DISABLE:
                if decision.result == AuthorizationResult.AUTO_APPROVED:
                    return False
        return True
