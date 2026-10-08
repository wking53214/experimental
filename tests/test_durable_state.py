"""Threat T13: limits must survive a restart, and a restart must not be a way to loosen them."""
import json

import pytest

from src.governance.audit_log import GENESIS, AuditIntegrityError, AuditLog, _entry_hash
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry
from src.governance.proposal import AdaptationDirection

ALICE = "alice-secret-1"


def registry():
    r = OperatorRegistry(iterations=1000)
    r.register("alice", ALICE)
    return r


def boot(tmp_path, **kw):
    return Governor(store_path=str(tmp_path / "events"), audit_path=str(tmp_path / "audit.jsonl"), **kw)


def flood(g, n=40, bid="api"):
    for _ in range(n):
        lim = g.boundaries.get_boundary(bid).current_limit
        g.execute_against_boundary(bid, lim * 1.05)
        p = g.detect_and_propose_adaptation(bid)
        if p:
            a, _ = g.authorize_proposal(p)
            g.apply_approved_proposal(a)


def loosen(g, to, who="alice"):
    p = g.proposals.create_proposal(
        boundary_id="api", source_evidence=[], current_value=g.boundaries.get_boundary("api").current_limit,
        proposed_value=to, reason="r", direction=AdaptationDirection.LOOSEN)
    g.submit_for_review(p)
    return g.apply_operator_decision(p.proposal_id, "approve_loosen", who, credential=ALICE)


def rewrite(path, entries):
    """What an attacker with write access can do: replace the file with a fully consistent chain."""
    prev = GENESIS
    lines = []
    for i, e in enumerate(entries):
        e["seq"], e["prev"] = i, prev
        e["hash"] = _entry_hash(prev, i, e["ts"], e["kind"], e["payload"])
        prev = e["hash"]
        lines.append(json.dumps(e, sort_keys=True, separators=(",", ":")))
    open(path, "w").write("\n".join(lines) + "\n")


class TestRestartKeepsTheRatchet:
    def test_a_tightened_limit_survives_a_restart(self, tmp_path):
        g = boot(tmp_path)
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        before = g.boundaries.get_boundary("api")
        g2 = boot(tmp_path)
        after = g2.boundaries.get_boundary("api")
        assert after.current_limit == before.current_limit == pytest.approx(72.9)
        assert after.version == before.version
        assert [v.current_limit for v in g2.boundaries.get_boundary_history("api").get_version_history()] == \
               [v.current_limit for v in g.boundaries.get_boundary_history("api").get_version_history()]
        assert g2.restored["boundaries"] == 1 and g2.verify_governance_integrity()[0]

    def test_create_boundary_after_a_restart_refuses_instead_of_resetting(self, tmp_path):
        g = boot(tmp_path)
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        g2 = boot(tmp_path)
        with pytest.raises(ValueError, match="already exists"):
            g2.boundaries.create_boundary("api", "rate", 100.0)
        assert g2.boundaries.get_boundary("api").current_limit == pytest.approx(72.9)

    def test_ensure_boundary_keeps_the_lower_restored_limit_and_says_so(self, tmp_path):
        g = boot(tmp_path)
        g.ensure_boundary("api", "rate", 100.0)
        flood(g)
        g2 = boot(tmp_path)
        v = g2.ensure_boundary("api", "rate", 100.0)  # the application's configured value
        assert v.current_limit == pytest.approx(72.9)
        assert g2.audit.find("configured_limit_ignored")
        g2.ensure_boundary("other", "rate", 10.0)  # new boundaries are created normally
        assert g2.boundaries.get_boundary("other").current_limit == 10.0

    def test_a_configured_limit_lower_than_restored_is_not_noise(self, tmp_path):
        g = boot(tmp_path)
        g.ensure_boundary("api", "rate", 100.0)
        g2 = boot(tmp_path)
        g2.ensure_boundary("api", "rate", 50.0)
        assert not g2.audit.find("configured_limit_ignored")  # lowering would be allowed; nothing to flag

    def test_the_breaker_state_survives_a_restart(self, tmp_path):
        g = boot(tmp_path)
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        assert g._auto_tightenings["api"] == 3
        g2 = boot(tmp_path)
        assert g2._auto_tightenings["api"] == 3
        flood(g2, 30)  # still held: a restart is not a way to reset the breaker
        assert g2.boundaries.get_boundary("api").current_limit == pytest.approx(72.9)
        g2.acknowledge_tightening("api", "alice")
        g3 = boot(tmp_path)
        assert g3._auto_tightenings["api"] == 0

    def test_an_authorized_loosening_is_restored_with_its_grant(self, tmp_path):
        g = boot(tmp_path, operators=registry())
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        loosen(g, 95.0)
        g2 = boot(tmp_path, operators=registry())
        assert g2.boundaries.get_boundary("api").current_limit == 95.0
        assert g2.boundaries.authorized_loosenings
        assert g2.boundaries.unauthorized_loosenings() == []
        assert g2.verify_governance_integrity()[0]

    def test_the_decision_list_is_rebuilt_so_the_cross_check_still_holds(self, tmp_path):
        g = boot(tmp_path, operators=registry())
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        loosen(g, 95.0)
        g2 = boot(tmp_path, operators=registry())
        assert len(g2.authority.decisions) == len(g.authority.decisions)
        assert g2.authority.audit_consistent()
        assert g2.authority.latest_decision(g.authority.decisions[-1].proposal_id).decided_by == "alice"

    def test_repeated_restarts_are_stable_and_visible(self, tmp_path):
        g = boot(tmp_path)
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        for _ in range(3):
            g = boot(tmp_path)
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(72.9)
        assert len(g.audit.find("restored")) == 3
        assert g.audit.verify()[0]

    def test_without_an_audit_path_nothing_is_persisted(self, tmp_path):
        """The old behavior, now an explicit choice: no audit_path means no durability."""
        g = Governor(store_path=str(tmp_path))
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        g2 = Governor(store_path=str(tmp_path))
        with pytest.raises(KeyError):
            g2.boundaries.get_boundary("api")


class TestStartupRefusesABadLog:
    def _prepared(self, tmp_path):
        g = boot(tmp_path, operators=registry())
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g)
        return g, str(tmp_path / "audit.jsonl")

    def test_an_edited_line_stops_startup(self, tmp_path):
        _, path = self._prepared(tmp_path)
        lines = open(path).read().splitlines()
        i = next(k for k, l in enumerate(lines) if '"boundary_version"' in l and '"version":3' in l)
        e = json.loads(lines[i]); e["payload"]["limit"] = 99.0; lines[i] = json.dumps(e)
        open(path, "w").write("\n".join(lines) + "\n")
        with pytest.raises(AuditIntegrityError, match="verification"):
            boot(tmp_path, operators=registry())

    def test_a_deleted_line_stops_startup(self, tmp_path):
        _, path = self._prepared(tmp_path)
        lines = open(path).read().splitlines()
        del lines[4]
        open(path, "w").write("\n".join(lines) + "\n")
        with pytest.raises(AuditIntegrityError):
            boot(tmp_path, operators=registry())

    def test_a_forged_but_consistent_loosening_without_a_grant_stops_startup(self, tmp_path):
        """The chain verifies (the attacker recomputed it), but replay refuses a loosening that has
        no grant on record."""
        g, path = self._prepared(tmp_path)
        entries = AuditLog(path).entries
        last = max(e["payload"]["version"] for e in entries if e["kind"] == "boundary_version")
        entries.append({"seq": 0, "ts": 1.0, "kind": "boundary_version", "prev": "", "hash": "",
                        "payload": {"boundary_id": "api", "version": last + 1, "resource": "rate",
                                    "limit": 500.0, "grant_id": None}})
        rewrite(path, entries)
        assert AuditLog(path).verify()[0]
        with pytest.raises(AuditIntegrityError, match="no grant"):
            boot(tmp_path, operators=registry())

    def test_a_forged_loosening_with_an_invented_grant_id_passes_replay_but_not_the_cross_check(self, tmp_path):
        """Documented limit: the log cannot prove a grant id is real. The integrity check does look for the
        matching authority decision and boundary_update record, which a forger must also fake."""
        g, path = self._prepared(tmp_path)
        entries = AuditLog(path).entries
        last = max(e["payload"]["version"] for e in entries if e["kind"] == "boundary_version")
        entries.append({"seq": 0, "ts": 1.0, "kind": "boundary_version", "prev": "", "hash": "",
                        "payload": {"boundary_id": "api", "version": last + 1, "resource": "rate",
                                    "limit": 500.0, "grant_id": "invented"}})
        rewrite(path, entries)
        g2 = boot(tmp_path, operators=registry())  # replay accepts it
        assert g2.boundaries.get_boundary("api").current_limit == 500.0
        assert not g2.verify_governance_integrity()[0]  # but no matching boundary_update with that grant

    def test_a_fully_rewritten_log_is_caught_by_the_anchor(self, tmp_path):
        g, path = self._prepared(tmp_path)
        anchor = g.audit_anchor()
        entries = AuditLog(path).entries
        entries = [e for e in entries if not (e["kind"] == "boundary_version" and e["payload"]["version"] >= 3)]
        rewrite(path, entries)
        boot(tmp_path, operators=registry())  # passes without an anchor
        with pytest.raises(AuditIntegrityError):
            boot(tmp_path, operators=registry(), audit_anchor=anchor)

    def test_a_crash_that_cut_the_last_write_is_repaired_not_fatal(self, tmp_path):
        g, path = self._prepared(tmp_path)
        n = len(g.audit.entries)
        with open(path, "a") as f:
            f.write('{"seq":%d,"ts":1.0,"kind":"boundary_vers' % n)  # half-written entry
        g2 = boot(tmp_path, operators=registry())
        assert g2.restored["truncated_tail_repaired"] is True
        assert g2.boundaries.get_boundary("api").current_limit == pytest.approx(72.9)
        assert AuditLog(path).verify()[0]  # the file was repaired and is still a valid chain

    def test_corruption_in_the_middle_is_fatal(self, tmp_path):
        _, path = self._prepared(tmp_path)
        lines = open(path).read().splitlines()
        lines[3] = "{not json"
        open(path, "w").write("\n".join(lines) + "\n")
        with pytest.raises(AuditIntegrityError, match="corrupt"):
            boot(tmp_path, operators=registry())

    def test_an_anchor_with_an_empty_log_is_refused(self, tmp_path):
        with pytest.raises(AuditIntegrityError):
            boot(tmp_path, audit_anchor={"length": 5, "head": "x"})


class TestWriteAheadBehavior:
    def test_if_the_log_write_fails_the_change_does_not_happen(self, tmp_path):
        g = boot(tmp_path)
        g.boundaries.create_boundary("api", "rate", 100.0)
        real = g.audit.append

        def failing(kind, payload):
            if kind == "boundary_version":
                raise OSError("disk full")
            return real(kind, payload)

        g.audit.append = failing
        with pytest.raises(OSError):
            g.boundaries.update_boundary("api", 90.0)
        assert g.boundaries.get_boundary("api").current_limit == 100.0
        with pytest.raises(OSError):
            g.boundaries.create_boundary("new", "rate", 5.0)
        assert "new" not in g.boundaries.boundaries
