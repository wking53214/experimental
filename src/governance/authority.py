"""
Authority Model — Sole System Gate for Auto-Approval

PURPOSE:
    Decide whether an adaptation proposal may be applied without a human.
    This is the architectural cut-point for asymmetric adaptive authority.

CONTRACT:
    - TIGHTEN  → system may AUTO_APPROVE
    - LOOSEN   → REQUIRES_HUMAN_REVIEW (system MUST NOT auto-approve)
    - DISABLE  → REQUIRES_HUMAN_REVIEW (system MUST NOT auto-approve)

CORRECTNESS RULE:
    No code path outside AuthorityModel may grant system auto-approval.
    Operator grants use record_operator_decision with decided_by=<operator_id>.

ARCHITECTURAL NOTE (Elegant H2):
    Operator path may record AUTO_APPROVED with decided_by != "system".
    Deferred: OPERATOR_APPROVED result value.
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import time

from .proposal import AdaptationProposal, AdaptationDirection, ProposalStatus


class AuthorizationResult(Enum):
    AUTO_APPROVED = "auto_approved"
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


class AuthorityModel:
    """Sole code path for system auto-approval decisions."""

    def __init__(self):
        self.decisions: list = []

    def evaluate_proposal(self, proposal: AdaptationProposal) -> AuthorizationResult:
        if proposal.direction == AdaptationDirection.TIGHTEN:
            return AuthorizationResult.AUTO_APPROVED
        if proposal.direction == AdaptationDirection.LOOSEN:
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW
        if proposal.direction == AdaptationDirection.DISABLE:
            return AuthorizationResult.REQUIRES_HUMAN_REVIEW
        return AuthorizationResult.REJECTED

    def authorize_proposal(self, proposal: AdaptationProposal) -> AuthorizationResult:
        result = self.evaluate_proposal(proposal)
        reason = {
            AuthorizationResult.AUTO_APPROVED: "TIGHTEN auto-approved by authority model",
            AuthorizationResult.REQUIRES_HUMAN_REVIEW: f"{proposal.direction.value} requires human review",
            AuthorizationResult.REJECTED: "Proposal rejected by authority model",
        }.get(result, "unknown")
        self.decisions.append(AuthorizationDecision(
            proposal_id=proposal.proposal_id,
            direction=proposal.direction,
            result=result,
            reason=reason,
            timestamp=time.time(),
            decided_by="system",
        ))
        return result

    def record_operator_decision(self, proposal, result, operator_id, rationale=""):
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
        for d in self.decisions:
            if d.direction == AdaptationDirection.LOOSEN:
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
