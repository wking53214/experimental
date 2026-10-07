"""
Adaptation proposals.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time
import uuid

from .grant import is_loosening


class AdaptationDirection(Enum):
    TIGHTEN = "tighten"
    LOOSEN = "loosen"
    DISABLE = "disable"


def effective_direction(current_value: Any, proposed_value: Any) -> AdaptationDirection:
    """Direction implied by the values themselves, ignoring any declared label."""
    return (AdaptationDirection.LOOSEN if is_loosening(current_value, proposed_value)
            else AdaptationDirection.TIGHTEN)


class ProposalStatus(Enum):
    PENDING = "pending"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


@dataclass
class AdaptationProposal:
    proposal_id: str
    boundary_id: str
    source_evidence: list
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
    def __init__(self):
        self.proposals: dict = {}
        self._pending = []
        self._approved = []
        self._rejected = []
        self._applied = []

    def create_proposal(
        self, boundary_id, source_evidence, current_value, proposed_value, reason, direction,
    ) -> AdaptationProposal:
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
        if proposal_id not in self.proposals:
            raise KeyError(f"Proposal {proposal_id} not found")
        return self.proposals[proposal_id]

    def mark_approved(self, proposal_id: str) -> AdaptationProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.PENDING_REVIEW):
            raise ValueError(
                f"Proposal {proposal_id} is not pending. Current status: {proposal.status.value}"
            )
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

    def mark_pending_review(self, proposal_id: str) -> AdaptationProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.PENDING_REVIEW):
            raise ValueError(
                f"Proposal {proposal_id} cannot enter review from {proposal.status.value}"
            )
        updated = AdaptationProposal(
            proposal_id=proposal.proposal_id,
            boundary_id=proposal.boundary_id,
            source_evidence=proposal.source_evidence,
            current_value=proposal.current_value,
            proposed_value=proposal.proposed_value,
            reason=proposal.reason,
            direction=proposal.direction,
            status=ProposalStatus.PENDING_REVIEW,
            created_at=proposal.created_at,
            notes=proposal.notes,
        )
        self.proposals[proposal_id] = updated
        return updated

    def mark_rejected(self, proposal_id: str) -> AdaptationProposal:
        proposal = self.get_proposal(proposal_id)
        if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.PENDING_REVIEW):
            raise ValueError(
                f"Proposal {proposal_id} is not pending. Current status: {proposal.status.value}"
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
        proposal = self.get_proposal(proposal_id)
        if proposal.status != ProposalStatus.APPROVED:
            raise ValueError(
                f"Proposal {proposal_id} is not approved. Current status: {proposal.status.value}"
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

    def get_pending_proposals(self):
        return [self.proposals[pid] for pid in self._pending]

    def get_all_proposals(self):
        return list(self.proposals.values())


class ProposalGenerator:
    def __init__(self, boundary_id, current_threshold=0.05, tighten_factor=0.9, loosen_factor=1.1):
        self.boundary_id = boundary_id
        self.current_threshold = current_threshold
        self.tighten_factor = tighten_factor
        self.loosen_factor = loosen_factor
        self.anomaly_history = []
        self.proposal_count = 0

    def generate_proposal(self, detection_result, evidence_ids):
        anomaly_detected = detection_result.get("anomaly_detected", False)
        gaming_detected = detection_result.get("gaming_detected", False)
        if not (anomaly_detected or gaming_detected):
            return None
        anomaly_score = detection_result.get("anomaly_score", 0.0)
        anomaly_count = detection_result.get("anomaly_count", 0)
        explanation = detection_result.get("explanation", "Unknown anomaly")
        self.anomaly_history.append({
            "timestamp": time.time(), "score": anomaly_score,
            "gaming": gaming_detected, "count": anomaly_count,
        })
        if len(self.anomaly_history) > 10:
            self.anomaly_history = self.anomaly_history[-10:]
        gaming_score = detection_result.get("gaming_score", 0.0)
        if gaming_detected and gaming_score > 0.7:
            direction = AdaptationDirection.TIGHTEN
            severity = 0.95
            reason = f"Pareto gaming detected: {explanation}"
        elif anomaly_count >= 3:
            direction = AdaptationDirection.TIGHTEN
            severity = 0.75
            reason = f"Multi-metric anomaly ({anomaly_count} metrics): {explanation}"
        elif anomaly_score > 0.85:
            direction = AdaptationDirection.TIGHTEN
            severity = 0.60
            reason = f"High-confidence anomaly detected: {explanation}"
        else:
            return None
        recent_anomalies = len(self.anomaly_history)
        if recent_anomalies >= 3:
            severity = min(1.0, severity + 0.2)
        proposed_value = self.current_threshold * self.tighten_factor ** severity
        self.proposal_count += 1
        return AdaptationProposal(
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
            },
        )

    def get_sustained_pattern(self):
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

    def reset_history(self):
        self.anomaly_history = []
