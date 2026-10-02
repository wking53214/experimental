"""Phase 10B/10C: Hybrid pipeline default path + operator adjudication."""
import time
import pytest
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection, ProposalStatus
from src.governance.authority import AuthorizationResult
from src.governance.phase9_integration import HybridDetectorPipeline


class TestPhase10BHybridDefault:
    def test_ingest_uses_hybrid_pipeline(self):
        gov = Governor(store_path="/tmp/p10b_hybrid", use_semantic=False)
        gov.boundaries.create_boundary("svc", "cpu", 100)
        for i in range(25):
            gov.ingest_metrics("svc", 1000.0 + i, {"cpu": 40.0 + (i % 3), "mem": 50.0})
        assert "svc" in gov.detector_pipelines
        assert isinstance(gov.detector_pipelines["svc"], HybridDetectorPipeline)

    def test_detect_from_pipeline_returns_none_when_calm(self):
        gov = Governor(store_path="/tmp/p10b_calm", use_semantic=False)
        gov.boundaries.create_boundary("svc", "cpu", 100)
        gov.patterns.create_pattern("p", "svc", 2, 60)
        for i in range(30):
            gov.ingest_metrics("svc", 1000.0 + i, {"cpu": 40.0, "mem": 50.0})
        prop = gov.detect_from_pipeline("svc")
        assert prop is None or prop.direction == AdaptationDirection.TIGHTEN

    def test_hybrid_attack_can_propose_tighten(self):
        gov = Governor(store_path="/tmp/p10b_atk", use_semantic=False)
        gov.boundaries.create_boundary("svc", "cpu", 100)
        gov.patterns.create_pattern("p", "svc", 2, 60)
        for i in range(30):
            gov.ingest_metrics("svc", 1000.0 + i, {"cpu": 40.0, "mem": 50.0})
        for _ in range(3):
            gov.execute_against_boundary("svc", 150)
        prop = gov.detect_and_propose_adaptation("svc")
        assert prop is not None
        assert prop.direction == AdaptationDirection.TIGHTEN
        _, result = gov.authorize_proposal(prop)
        assert result == AuthorizationResult.AUTO_APPROVED
        gov.apply_approved_proposal(prop)
        b = gov.boundaries.get_boundary("svc")
        assert b.current_limit < 100


class TestPhase10CAdjudication:
    def _make_loosen_proposal(self, gov, boundary_id="cpu"):
        gov.boundaries.create_boundary(boundary_id, "cpu_percent", 50)
        return gov.proposals.create_proposal(
            boundary_id=boundary_id,
            source_evidence=[],
            current_value=50,
            proposed_value=80,
            reason="migration window",
            direction=AdaptationDirection.LOOSEN,
        )

    def test_loosen_requires_human_review(self):
        gov = Governor(store_path="/tmp/p10c_req")
        prop = self._make_loosen_proposal(gov)
        _, result = gov.authorize_proposal(prop)
        assert result == AuthorizationResult.REQUIRES_HUMAN_REVIEW
        assert gov.authority.verify_no_auto_loosen()

    def test_submit_for_review(self):
        gov = Governor(store_path="/tmp/p10c_sub")
        prop = self._make_loosen_proposal(gov)
        updated = gov.submit_for_review(prop)
        assert updated.status == ProposalStatus.PENDING_REVIEW
        pending = gov.list_pending_review()
        assert any(p.proposal_id == prop.proposal_id for p in pending)

    def test_operator_approve_loosen(self):
        gov = Governor(store_path="/tmp/p10c_approve")
        prop = self._make_loosen_proposal(gov)
        gov.submit_for_review(prop)
        approved, version = gov.apply_operator_decision(
            prop.proposal_id, "approve_loosen", "operator_alice", "migration window"
        )
        assert approved.status in (ProposalStatus.APPLIED, ProposalStatus.APPROVED)
        b = gov.boundaries.get_boundary("cpu")
        assert b.current_limit == 80
        assert gov.authority.verify_no_auto_loosen()
        decisions = gov.authority.get_decisions()
        assert any(d.decided_by == "operator_alice" for d in decisions)

    def test_operator_reject_loosen(self):
        gov = Governor(store_path="/tmp/p10c_reject")
        prop = self._make_loosen_proposal(gov)
        gov.submit_for_review(prop)
        rejected, result = gov.apply_operator_decision(
            prop.proposal_id, "reject", "operator_bob", "not justified"
        )
        assert result == AuthorizationResult.REJECTED
        assert rejected.status == ProposalStatus.REJECTED
        b = gov.boundaries.get_boundary("cpu")
        assert b.current_limit == 50

    def test_evidence_pack(self):
        gov = Governor(store_path="/tmp/p10c_evidence")
        prop = self._make_loosen_proposal(gov)
        gov.execute_against_boundary("cpu", 60)
        pack = gov.get_evidence_pack(prop.proposal_id)
        assert pack["proposal"]["proposal_id"] == prop.proposal_id
        assert pack["proposal"]["direction"] == "loosen"
        assert "version_chain" in pack
        assert "recent_violations" in pack

    def test_tighten_cannot_submit_for_review(self):
        gov = Governor(store_path="/tmp/p10c_tighten")
        gov.boundaries.create_boundary("cpu", "cpu", 100)
        prop = gov.proposals.create_proposal(
            boundary_id="cpu",
            source_evidence=[],
            current_value=100,
            proposed_value=90,
            reason="tighten",
            direction=AdaptationDirection.TIGHTEN,
        )
        with pytest.raises(ValueError):
            gov.submit_for_review(prop)
