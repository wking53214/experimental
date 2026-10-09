"""Bring-your-own-monitoring interface (docs/INTEGRATION.md): it can tighten, it can ask, it can never loosen."""
import math
import random

import pytest

from src.governance.audit_log import AuditLog
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

MON, OPS = "monitor-secret-1", "alice-secret-1"


def make(tmp_path, durable=False, **kw):
    sources = OperatorRegistry(iterations=1000)
    sources.register("monitor", MON)
    ops = OperatorRegistry(iterations=1000)
    ops.register("alice", OPS)
    extra = {"audit_path": str(tmp_path / "audit.jsonl")} if durable else {}
    g = Governor(store_path=str(tmp_path / "ev"), signal_sources=sources, operators=ops, **extra, **kw)
    g.ensure_boundary("api", "rate", 100.0)
    return g


def tighten(g, **kw):
    kw.setdefault("source", "monitor")
    kw.setdefault("credential", MON)
    return g.propose_tightening("api", **kw)


class TestTightening:
    def test_applies_an_absolute_limit_and_reports_the_new_state(self, tmp_path):
        g = make(tmp_path)
        r = tighten(g, new_limit=80, reason="error rate up", evidence={"error_rate": 0.31})
        assert r["status"] == "applied" and r["limit_before"] == 100.0 and r["limit_after"] == 80.0
        assert r["identity_verified"] is True and r["remaining_before_breaker"] == 2
        assert g.boundaries.get_boundary("api").current_limit == 80.0
        e = g.audit.find("signal")[-1]["payload"]
        assert e["source"] == "monitor" and e["evidence"] == {"error_rate": 0.31} and e["applied"] == 80.0

    def test_applies_a_factor(self, tmp_path):
        g = make(tmp_path)
        assert tighten(g, factor=0.9)["limit_after"] == pytest.approx(90.0)

    def test_it_cannot_raise_a_limit(self, tmp_path):
        g = make(tmp_path)
        tighten(g, new_limit=80)
        for target in (80, 81, 100, 1e9):
            r = tighten(g, new_limit=target)
            assert r["status"] == "rejected" and "request_loosening" in r["reason"]
        assert g.boundaries.get_boundary("api").current_limit == 80.0

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.5, 1.5, float("nan"), float("inf"), True, "0.9", None])
    def test_bad_factors_are_errors_not_changes(self, tmp_path, bad):
        g = make(tmp_path)
        with pytest.raises(ValueError):
            tighten(g, factor=bad)
        assert g.boundaries.get_boundary("api").current_limit == 100.0

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "50", [50], {"a": 1}])
    def test_bad_limits_are_errors_not_changes(self, tmp_path, bad):
        g = make(tmp_path)
        with pytest.raises(ValueError):
            tighten(g, new_limit=bad)
        assert g.boundaries.get_boundary("api").current_limit == 100.0

    def test_exactly_one_of_limit_or_factor(self, tmp_path):
        g = make(tmp_path)
        with pytest.raises(ValueError):
            tighten(g)
        with pytest.raises(ValueError):
            tighten(g, new_limit=50, factor=0.5)

    def test_zero_and_negative_limits_are_clamped_to_the_floor_not_applied(self, tmp_path):
        g = make(tmp_path)
        r = tighten(g, new_limit=0.0)
        assert r["status"] == "applied_clamped" and r["limit_after"] == pytest.approx(20.0)
        r = tighten(g, new_limit=-50.0)
        assert r["status"] == "at_floor" and r["limit_after"] == pytest.approx(20.0)
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(20.0)

    def test_reaching_the_floor_then_asking_for_more_says_at_floor(self, tmp_path):
        g = make(tmp_path, max_auto_tightenings=None)
        assert tighten(g, new_limit=1)["status"] == "applied_clamped"
        r = tighten(g, new_limit=1)
        assert r["status"] == "at_floor" and r["limit_after"] == pytest.approx(20.0)

    def test_unknown_boundary_and_missing_source_are_errors(self, tmp_path):
        g = make(tmp_path)
        with pytest.raises(KeyError):
            g.propose_tightening("nope", source="monitor", credential=MON, factor=0.9)
        with pytest.raises(ValueError):
            g.propose_tightening("api", source="", credential=MON, factor=0.9)

    def test_input_size_limits(self, tmp_path):
        g = make(tmp_path)
        with pytest.raises(ValueError):
            tighten(g, factor=0.9, reason="x" * 501)
        with pytest.raises(ValueError):
            tighten(g, factor=0.9, evidence={"blob": "x" * 5000})
        with pytest.raises(ValueError):
            tighten(g, factor=0.9, evidence={"f": object()})
        with pytest.raises(ValueError):
            tighten(g, factor=0.9, idempotency_key="k" * 200)
        assert g.boundaries.get_boundary("api").current_limit == 100.0


class TestSharedGuards:
    def test_the_breaker_applies_and_a_hold_is_one_record(self, tmp_path):
        g = make(tmp_path)
        statuses = [tighten(g, factor=0.95)["status"] for _ in range(10)]
        assert statuses[:3] == ["applied"] * 3 and set(statuses[3:]) == {"held"}
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(100 * 0.95 ** 3)
        assert len(g.audit.find("tightening_held")) == 1
        g.acknowledge_tightening("api", "alice", credential=OPS)
        assert tighten(g, factor=0.95)["status"] == "applied"

    def test_external_signals_and_the_built_in_loop_share_one_budget(self, tmp_path):
        g = make(tmp_path)
        for _ in range(8):
            lim = g.boundaries.get_boundary("api").current_limit
            g.execute_against_boundary("api", lim * 1.05)
            p = g.detect_and_propose_adaptation("api")
            if p:
                a, _ = g.authorize_proposal(p)
                g.apply_approved_proposal(a)
        assert g._auto_tightenings["api"] == 3
        assert tighten(g, factor=0.9)["status"] == "held"

    def test_cooldown_applies(self, tmp_path):
        now = [0.0]
        g = make(tmp_path, tighten_cooldown_s=100.0, clock=lambda: now[0])
        assert tighten(g, factor=0.9)["status"] == "applied"
        assert tighten(g, factor=0.9)["status"] == "held"
        now[0] += 101.0
        assert tighten(g, factor=0.9)["status"] == "applied"

    def test_integrity_and_audit_hold_after_a_busy_session(self, tmp_path):
        g = make(tmp_path)
        for i in range(6):
            tighten(g, factor=0.95, reason=f"r{i}")
        g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=100, reason="recovered")
        ok, checks = g.verify_governance_integrity()
        assert ok, checks


class TestIdempotency:
    def test_a_retry_with_the_same_key_does_not_tighten_twice(self, tmp_path):
        g = make(tmp_path)
        a = tighten(g, factor=0.9, idempotency_key="evt-1")
        b = tighten(g, factor=0.9, idempotency_key="evt-1")
        assert a["status"] == "applied" and b["duplicate"] is True
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(90.0)
        assert tighten(g, factor=0.9, idempotency_key="evt-2")["status"] == "applied"

    def test_the_key_survives_a_restart(self, tmp_path):
        g = make(tmp_path, durable=True)
        tighten(g, factor=0.9, idempotency_key="evt-1")
        g2 = make(tmp_path, durable=True)
        r = tighten(g2, factor=0.9, idempotency_key="evt-1")
        assert r["duplicate"] is True and r["limit_after"] == pytest.approx(90.0)
        assert g2.boundaries.get_boundary("api").current_limit == pytest.approx(90.0)

    def test_the_key_is_in_the_same_log_entry_as_the_change(self, tmp_path):
        """A crash between the change and any later bookkeeping cannot lose the key."""
        g = make(tmp_path, durable=True)
        tighten(g, factor=0.9, idempotency_key="evt-9")
        bv = [e for e in AuditLog(str(tmp_path / "audit.jsonl")).find("boundary_version")
              if e["payload"]["version"] == 2][0]["payload"]
        assert bv["signal_key"] == "evt-9" and bv["source"] == "monitor"


class TestAuthentication:
    def test_sources_need_their_own_credential(self, tmp_path):
        g = make(tmp_path)
        for who, cred in (("monitor", None), ("monitor", "wrong"), ("monitor", OPS), ("stranger", MON)):
            with pytest.raises(PermissionError):
                g.propose_tightening("api", source=who, credential=cred, factor=0.9)
        assert g.boundaries.get_boundary("api").current_limit == 100.0
        assert g.audit.find("signal_auth_failed")

    def test_a_source_credential_is_not_an_operator_credential(self, tmp_path):
        g = make(tmp_path)
        r = g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=150)
        with pytest.raises(PermissionError):
            g.apply_operator_decision(r["proposal_id"], "approve_loosen", "monitor", credential=MON)
        with pytest.raises(PermissionError):
            g.acknowledge_tightening("api", "monitor", credential=MON)
        assert g.boundaries.get_boundary("api").current_limit == 100.0

    def test_without_a_source_registry_the_name_is_only_a_claim(self, tmp_path):
        g = Governor(store_path=str(tmp_path))
        g.ensure_boundary("api", "rate", 100.0)
        r = g.propose_tightening("api", source="anything", factor=0.9)
        assert r["status"] == "applied" and r["identity_verified"] is False


class TestLoosening:
    def test_a_request_never_changes_the_limit_and_waits_for_an_operator(self, tmp_path):
        g = make(tmp_path)
        tighten(g, factor=0.8)
        r = g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=100, reason="recovered")
        assert r["status"] == "pending_review" and r["limit_after"] == pytest.approx(80.0)
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(80.0)
        assert r["proposal_id"] in [p.proposal_id for p in g.list_pending_review()]

    def test_the_operator_can_then_approve_it_with_their_credential(self, tmp_path):
        g = make(tmp_path)
        tighten(g, factor=0.8)
        r = g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=100)
        g.apply_operator_decision(r["proposal_id"], "approve_loosen", "alice", credential=OPS)
        assert g.boundaries.get_boundary("api").current_limit == 100.0
        assert g.verify_governance_integrity()[0]

    def test_a_request_that_is_not_a_loosening_is_rejected(self, tmp_path):
        g = make(tmp_path)
        for target in (100, 90, 0):
            assert g.request_loosening("api", requested_by="monitor", credential=MON,
                                       new_limit=target)["status"] == "rejected"
        assert g.list_pending_review() == []

    def test_pending_requests_are_capped(self, tmp_path):
        g = make(tmp_path)
        out = [g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=101 + i)["status"]
               for i in range(25)]
        assert out.count("pending_review") == 20 and out.count("rejected") == 5


class TestNeverLoosens:
    def test_randomized_hostile_calls_can_only_ever_lower_the_limit(self, tmp_path):
        """200 random calls, mixing legitimate and hostile inputs. Invariant: the limit is
        non-increasing and never below the floor, and the integrity check passes."""
        rnd = random.Random(7)
        g = make(tmp_path, max_auto_tightenings=None)
        last = 100.0
        weird = [float("nan"), float("inf"), -1e300, 1e300, 0, -0.0, 5e-324, True, None, "9", [1], 1e-9]
        for i in range(200):
            kind = rnd.choice(["limit", "factor", "loosen", "weird"])
            try:
                if kind == "limit":
                    g.propose_tightening("api", source="monitor", credential=MON, new_limit=rnd.uniform(-50, 300))
                elif kind == "factor":
                    g.propose_tightening("api", source="monitor", credential=MON, factor=rnd.uniform(-1, 2))
                elif kind == "loosen":
                    g.request_loosening("api", requested_by="monitor", credential=MON, new_limit=rnd.uniform(0, 500))
                else:
                    g.propose_tightening("api", source="monitor", credential=MON, **{rnd.choice(["new_limit", "factor"]): rnd.choice(weird)})
            except (ValueError, KeyError, PermissionError):
                pass
            now = g.boundaries.get_boundary("api").current_limit
            assert math.isfinite(now) and now <= last and now >= 20.0 - 1e-9, (i, kind, last, now)
            last = now
        assert g.boundaries.unauthorized_loosenings() == []
        assert g.verify_governance_integrity()[0]
