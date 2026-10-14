"""Anchor store (src/governance/anchor.py; docs/THREAT_MODEL.md, T8): the audit log's head is published somewhere
else after every append, and startup verifies the log against the latest published head. What it catches, what it
does not, and that it fails closed."""
import hashlib
import json

import pytest

from src.governance.anchor import AnchorStoreError, FileAnchorStore
from src.governance.audit_log import AuditIntegrityError, _canonical, _entry_hash, GENESIS
from src.governance.governor import Governor


def make(tmp_path, **kw):
    store = FileAnchorStore(str(tmp_path / "anchors.jsonl"))
    g = Governor(store_path=str(tmp_path / "ev"), audit_path=str(tmp_path / "audit.jsonl"), anchor_store=store, **kw)
    return g, store


def work(g):
    g.ensure_boundary("b", "cpu", 100.0)
    g.propose_tightening("b", source="s", factor=0.9, reason="r", idempotency_key="k1")
    assert g.boundary_status("b")["limit"] == 90.0


def rewrite(path, mutate):
    """Rewrite the whole log with one entry changed and every hash recomputed: a forger with write access."""
    entries = [json.loads(l) for l in open(path) if l.strip()]
    mutate(entries)
    prev = GENESIS
    for e in entries:
        e["prev"] = prev
        e["hash"] = _entry_hash(prev, e["seq"], e["ts"], e["kind"], e["payload"])
        prev = e["hash"]
    with open(path, "w") as f:
        f.write("".join(_canonical(e) + "\n" for e in entries))


class TestAnchorStore:
    def test_every_append_publishes_the_new_head(self, tmp_path):
        g, store = make(tmp_path)
        work(g)
        assert store.latest() == g.audit.anchor()
        lines = [l for l in open(store.path) if l.strip()]
        assert len(lines) >= len(g.audit.entries)

    def test_a_clean_restart_verifies_against_the_latest_anchor_and_keeps_the_limit(self, tmp_path):
        g, store = make(tmp_path)
        work(g)
        g2, _ = make(tmp_path)
        assert g2.boundary_status("b")["limit"] == 90.0 and g2.restored["entries"] >= 1

    def test_a_full_rewrite_of_the_log_is_refused_at_startup(self, tmp_path):
        """The forgery verifies as a chain (every hash recomputed) but its history differs from the anchored one."""
        g, store = make(tmp_path)
        work(g)

        def forge(entries):
            for e in entries:
                if e["kind"] == "boundary_version" and e["payload"].get("limit") == 90.0:
                    e["payload"]["limit"] = 95.0
        rewrite(g.audit.path, forge)
        from src.governance.audit_log import AuditLog
        assert AuditLog(g.audit.path).verify()[0]                   # looks fine without the anchor
        with pytest.raises(AuditIntegrityError):
            make(tmp_path)

    def test_a_truncated_log_is_refused_at_startup(self, tmp_path):
        g, store = make(tmp_path)
        work(g)
        lines = [l for l in open(g.audit.path) if l.strip()]
        with open(g.audit.path, "w") as f:
            f.write("".join(lines[:-2]))
        with pytest.raises(AuditIntegrityError):
            make(tmp_path)

    def test_a_deleted_log_with_a_published_anchor_is_refused(self, tmp_path):
        g, store = make(tmp_path)
        work(g)
        (tmp_path / "audit.jsonl").unlink()
        with pytest.raises(AuditIntegrityError):
            make(tmp_path)

    def test_the_limit_an_attacker_who_can_also_rewrite_the_anchors_is_not_caught(self, tmp_path):
        """Documented limit: the store only helps where the process cannot rewrite it."""
        g, store = make(tmp_path)
        work(g)

        def forge(entries):
            for e in entries:
                if e["kind"] == "boundary_version" and e["payload"].get("limit") == 90.0:
                    e["payload"]["limit"] = 95.0
        rewrite(g.audit.path, forge)
        from src.governance.audit_log import AuditLog
        forged = AuditLog(g.audit.path)
        (tmp_path / "anchors.jsonl").unlink()
        store.publish(forged.anchor())                              # the attacker re-anchors the forgery
        g2, _ = make(tmp_path)
        assert g2.boundary_status("b")["limit"] == 95.0             # accepted: nothing left to compare against

    def test_an_explicit_anchor_wins_over_the_store(self, tmp_path):
        g, store = make(tmp_path)
        work(g)
        early = g.audit.anchor()
        g.propose_tightening("b", source="s", factor=0.9, reason="r", idempotency_key="k2")
        g2 = Governor(store_path=str(tmp_path / "ev"), audit_path=str(tmp_path / "audit.jsonl"),
                      anchor_store=store, audit_anchor=early)      # an older trusted anchor still verifies
        assert g2.boundary_status("b")["limit"] == 81.0
        with pytest.raises(AuditIntegrityError):
            Governor(store_path=str(tmp_path / "ev"), audit_path=str(tmp_path / "audit.jsonl"),
                     audit_anchor={"length": early["length"], "head": "0" * 64})


class TestFailClosed:
    def test_an_append_that_cannot_be_anchored_fails_and_the_change_does_not_happen(self, tmp_path):
        g, store = make(tmp_path)
        g.ensure_boundary("b", "cpu", 100.0)
        before = len(g.audit.entries)
        store.path = str(tmp_path / "no-such-dir" / "anchors.jsonl")   # the store becomes unwritable
        with pytest.raises(AuditIntegrityError):
            g.propose_tightening("b", source="s", factor=0.9, reason="r", idempotency_key="k1")
        assert g.boundary_status("b")["limit"] == 100.0
        assert len(g.audit.entries) == before
        assert g.audit.verify()[0]
        lines = [l for l in open(g.audit.path) if l.strip()]
        assert len(lines) == before                                  # the file was rolled back too

    def test_an_unreadable_store_is_an_error_not_an_absence(self, tmp_path):
        g, store = make(tmp_path)
        work(g)
        with open(store.path, "w") as f:
            f.write("{not json\n")
        with pytest.raises(AnchorStoreError):
            make(tmp_path)

    @pytest.mark.parametrize("bad", [None, {}, {"length": "3", "head": "x"}, {"length": 3}, {"head": "x"}])
    def test_malformed_anchors_are_refused_by_the_store(self, tmp_path, bad):
        store = FileAnchorStore(str(tmp_path / "a.jsonl"))
        with pytest.raises(AnchorStoreError):
            store.publish(bad)
        assert store.latest() is None
