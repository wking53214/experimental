"""T8 (tamper evidence) and T9 (verified operator identity). See docs/THREAT_MODEL.md."""
import copy
import json

import pytest

from src.governance.audit_log import GENESIS, AuditLog, _entry_hash
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry
from src.governance.proposal import AdaptationDirection
from src.governance.rollback import RollbackExecutor, RollbackReason


def _log(n=5):
    log = AuditLog()
    for i in range(n):
        log.append("event", {"i": i, "who": "system"})
    return log


def _rehash(entries):
    """What a writer with full access can do: rewrite everything and recompute every hash."""
    prev = GENESIS
    for i, e in enumerate(entries):
        e["seq"], e["prev"] = i, prev
        e["hash"] = _entry_hash(prev, i, e["ts"], e["kind"], e["payload"])
        prev = e["hash"]


class TestAuditLog:
    def test_clean_log_verifies(self):
        assert _log().verify() == (True, None)
        assert AuditLog().verify() == (True, None)

    def test_editing_a_past_entry_is_detected_at_that_entry(self):
        log = _log()
        log.entries[2]["payload"]["who"] = "operator"
        ok, problem = log.verify()
        assert not ok and "entry 2" in problem

    def test_deleting_an_entry_is_detected(self):
        log = _log()
        del log.entries[1]
        assert not log.verify()[0]

    def test_reordering_is_detected(self):
        log = _log()
        log.entries[1], log.entries[2] = log.entries[2], log.entries[1]
        assert not log.verify()[0]

    def test_inserting_a_forged_entry_is_detected(self):
        log = _log()
        forged = copy.deepcopy(log.entries[1])
        forged["payload"] = {"i": 99}
        log.entries.insert(2, forged)
        assert not log.verify()[0]

    def test_full_rewrite_with_recomputed_hashes_passes_alone_but_not_against_an_anchor(self):
        log = _log()
        anchor = log.anchor()
        log.entries[2]["payload"]["who"] = "operator"
        _rehash(log.entries)
        assert log.verify()[0], "a full rewrite is invisible without an anchor (documented limit)"
        ok, problem = log.verify(anchor)
        assert not ok and "differs from the anchored history" in problem

    def test_truncation_needs_an_anchor_to_be_seen(self):
        log = _log()
        anchor = log.anchor()
        del log.entries[3:]
        assert log.verify()[0]
        ok, problem = log.verify(anchor)
        assert not ok and "anchor was taken" in problem

    def test_appending_after_the_anchor_is_fine(self):
        log = _log()
        anchor = log.anchor()
        log.append("event", {"later": True})
        assert log.verify(anchor) == (True, None)

    def test_file_round_trip_and_on_disk_tampering(self, tmp_path):
        path = str(tmp_path / "audit.jsonl")
        log = AuditLog(path)
        for i in range(4):
            log.append("event", {"i": i})
        reloaded = AuditLog(path)
        assert reloaded.head == log.head and reloaded.verify()[0]
        lines = open(path).read().splitlines()
        e = json.loads(lines[1]); e["payload"]["i"] = 42; lines[1] = json.dumps(e)
        open(path, "w").write("\n".join(lines) + "\n")
        assert not AuditLog(path).verify()[0]


class TestOperatorRegistry:
    def test_register_authenticate_identify_revoke(self):
        r = OperatorRegistry(iterations=1000)
        r.register("alice", "alice-secret-1")
        r.register("bob", "bob-secret-22")
        assert r.authenticate("alice", "alice-secret-1")
        assert not r.authenticate("alice", "bob-secret-22")
        assert not r.authenticate("alice", "")
        assert not r.authenticate("mallory", "alice-secret-1")
        assert r.identify("bob-secret-22") == "bob" and r.identify("nope") is None
        r.revoke("alice")
        assert not r.authenticate("alice", "alice-secret-1")

    def test_rejects_weak_or_duplicate_registration(self):
        r = OperatorRegistry(iterations=1000)
        with pytest.raises(ValueError):
            r.register("a", "short")
        with pytest.raises(ValueError):
            r.register("", "long-enough-secret")
        r.register("a", "long-enough-secret")
        with pytest.raises(ValueError):
            r.register("a", "another-long-secret")

    def test_credentials_are_not_stored(self):
        r = OperatorRegistry(iterations=1000)
        r.register("alice", "alice-secret-1")
        assert "alice-secret-1" not in repr(r._records)


def _gov(tmp_path, **kw):
    reg = OperatorRegistry(iterations=1000)
    reg.register("alice", "alice-secret-1")
    reg.register("bob", "bob-secret-22")
    g = Governor(store_path=str(tmp_path), operators=reg, **kw)
    g.boundaries.create_boundary("api", "rate", 50.0)
    return g


def _loosen(g, to=80.0):
    p = g.proposals.create_proposal(
        boundary_id="api", source_evidence=[], current_value=g.boundaries.get_boundary("api").current_limit,
        proposed_value=to, reason="r", direction=AdaptationDirection.LOOSEN)
    return g.submit_for_review(p)


class TestVerifiedIdentity:
    def test_loosening_needs_the_operators_own_credential(self, tmp_path):
        g = _gov(tmp_path)
        p = _loosen(g)
        for bad in (None, "", "bob-secret-22", "guess"):
            with pytest.raises(PermissionError):
                g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential=bad)
        assert g.boundaries.get_boundary("api").current_limit == 50.0
        assert g.authority.grants == {} and g.authority.latest_decision(p.proposal_id) is None
        _, nv = g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential="alice-secret-1")
        assert nv.current_limit == 80.0
        d = g.authority.latest_decision(p.proposal_id)
        assert d.decided_by == "alice" and d.identity_verified

    def test_failed_attempts_are_audited_and_leave_no_operator_record(self, tmp_path):
        g = _gov(tmp_path)
        p = _loosen(g)
        with pytest.raises(PermissionError):
            g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential="nope")
        assert g.audit.find("operator_auth_failed")
        assert g.authority.latest_decision(p.proposal_id) is None
        assert not list(g.file_store.decisions_dir.glob(f"{p.proposal_id}_operator_*"))

    def test_rejections_also_need_authentication(self, tmp_path):
        g = _gov(tmp_path)
        p = _loosen(g)
        with pytest.raises(PermissionError):
            g.apply_operator_decision(p.proposal_id, "reject", "alice", credential=None)

    def test_without_a_registry_behavior_is_unchanged(self, tmp_path):
        g = Governor(store_path=str(tmp_path))
        g.boundaries.create_boundary("api", "rate", 50.0)
        p = _loosen(g)
        _, nv = g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice")
        assert nv.current_limit == 80.0
        assert g.authority.latest_decision(p.proposal_id).identity_verified is False

    def test_breaker_acknowledgement_needs_credentials_when_a_registry_exists(self, tmp_path):
        g = _gov(tmp_path)
        with pytest.raises(PermissionError):
            g.acknowledge_tightening("api", "alice", credential="wrong")
        g.acknowledge_tightening("api", "alice", credential="alice-secret-1")
        assert g.audit.find("tightening_acknowledged")[-1]["payload"]["identity_verified"] is True

    def test_operator_initiated_rollback_needs_credentials(self, tmp_path):
        g = _gov(tmp_path)
        # tighten to 40 automatically, then roll back (a loosening) as alice
        t = g.proposals.create_proposal(boundary_id="api", source_evidence=[], current_value=50.0,
                                        proposed_value=40.0, reason="t", direction=AdaptationDirection.TIGHTEN)
        a, _ = g.authorize_proposal(t)
        g.apply_approved_proposal(a)
        rb = RollbackExecutor(g)
        with pytest.raises(PermissionError, match="could not be authenticated"):
            rb.execute_rollback(t.proposal_id, "api", RollbackReason.MANUAL_REQUEST,
                                operator_id="alice", credential="nope")
        assert g.boundaries.get_boundary("api").current_limit == 40.0
        done = rb.execute_rollback(t.proposal_id, "api", RollbackReason.MANUAL_REQUEST,
                                   operator_id="alice", credential="alice-secret-1")
        assert done is not None and g.boundaries.get_boundary("api").current_limit == 50.0


class TestGovernorTamperEvidence:
    def _run(self, tmp_path):
        g = _gov(tmp_path)
        t = g.proposals.create_proposal(boundary_id="api", source_evidence=[], current_value=50.0,
                                        proposed_value=45.0, reason="t", direction=AdaptationDirection.TIGHTEN)
        a, _ = g.authorize_proposal(t)
        g.apply_approved_proposal(a)
        p = _loosen(g, 60.0)
        g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential="alice-secret-1")
        return g

    def test_honest_run_passes_every_integrity_check(self, tmp_path):
        g = self._run(tmp_path)
        ok, checks = g.verify_governance_integrity()
        assert ok, checks
        names = [n for n, _ in checks]
        assert "Audit log hash chain intact" in names and "Decision list matches audit log" in names

    def test_every_decision_and_boundary_change_is_logged(self, tmp_path):
        g = self._run(tmp_path)
        assert len(g.audit.find("authority_decision")) == len(g.authority.decisions) == 2
        updates = g.audit.find("boundary_update")
        assert [u["payload"]["new_limit"] for u in updates] == [45.0, 60.0]
        assert updates[1]["payload"]["grant_id"] == g.authority.decisions[-1].grant_id

    def test_editing_the_decision_list_is_caught(self, tmp_path):
        g = self._run(tmp_path)
        g.authority.decisions[0].decided_by = "someone-else"
        assert not g.verify_governance_integrity()[0]

    def test_deleting_a_decision_is_caught(self, tmp_path):
        g = self._run(tmp_path)
        g.authority.decisions.pop()
        assert not g.verify_governance_integrity()[0]

    def test_editing_the_audit_log_is_caught(self, tmp_path):
        g = self._run(tmp_path)
        g.audit.entries[0]["payload"]["decided_by"] = "operator"
        assert not g.verify_governance_integrity()[0]

    def test_a_loosening_missing_from_the_log_is_caught(self, tmp_path):
        g = self._run(tmp_path)
        g.audit.entries = [e for e in g.audit.entries
                           if not (e["kind"] == "boundary_update" and e["payload"]["new_limit"] == 60.0)]
        _rehash(g.audit.entries)  # even a consistent rewrite of the chain does not hide it
        assert not g.verify_governance_integrity()[0]

    def test_anchor_catches_a_full_rewrite(self, tmp_path):
        g = self._run(tmp_path)
        anchor = g.audit_anchor()
        g.audit.entries[0]["payload"]["reason"] = "forged"
        _rehash(g.audit.entries)
        assert g.audit.verify()[0] and not g.audit.verify(anchor)[0]

    def test_audit_log_persists_to_a_file(self, tmp_path):
        path = str(tmp_path / "gov_audit.jsonl")
        g = _gov(tmp_path, audit_path=path)
        p = _loosen(g)
        g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential="alice-secret-1")
        assert AuditLog(path).head == g.audit.head and AuditLog(path).verify()[0]
