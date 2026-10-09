"""DGK with governed health limits (integrations/dgk_governed.py). Runs against the real DGK; skipped if it
is not installed (CI installs a pinned commit in a separate job)."""
import math
import os
import random
import secrets

import pytest

dgk = pytest.importorskip("dgk")

from integrations.dgk_governed import (GovernedHealthLimitCheck, GovernedKernel, SustainedStressPolicy,
                                       HEALTH_LIMITS)
from src.governance.audit_log import AuditLog
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry
from src.governance.proposal import AdaptationDirection

CALM = {"latency": 100.0, "abort_rate": 0.01, "reentry_rate": 0.1, "load_depth": 100.0, "determinism_index": 0.99}
STRESS = {"latency": 450.0, "abort_rate": 0.22, "reentry_rate": 1.8, "load_depth": 4000.0, "determinism_index": 0.5}
OPS = "alice-secret-1"


def _stock_has_defects():
    """True for DGK before its telemetry validation fix (da256f3); False once it refuses NaN itself."""
    import tempfile
    k = dgk.Kernel(log_path=os.path.join(tempfile.mkdtemp(), "probe.log"))
    tok = secrets.token_urlsafe(32)
    k.callers.register("ops", tok, partitions=["p1"])
    r = k.process_transaction("p1", {**CALM, "latency": float("nan")}, "hi", "ops", tok)
    return r["transaction_status"] == "COMMITTED"


STOCK_HAS_DEFECTS = _stock_has_defects()
needs_unfixed_dgk = pytest.mark.skipif(
    not STOCK_HAS_DEFECTS, reason="this DGK already validates telemetry (4a0ecd2 or later)")


def kernel(tmp_path, name="k"):
    k = dgk.Kernel(log_path=str(tmp_path / f"{name}.log"))
    tok = secrets.token_urlsafe(32)
    k.callers.register("ops", tok, partitions=["p1"])
    return k, tok


def governor(tmp_path, **kw):
    ops = OperatorRegistry(iterations=1000)
    ops.register("alice", OPS)
    return Governor(store_path=str(tmp_path / "ev"), audit_path=str(tmp_path / "gov.jsonl"), operators=ops, **kw)


def governed(tmp_path, **kw):
    k, tok = kernel(tmp_path)
    g = governor(tmp_path)
    return GovernedKernel(k, g, **kw), g, k, tok


def tx(gk, tok, tele=None, text="hello"):
    return gk.process_transaction("p1", {**CALM, **(tele or {})}, text, "ops", tok)


class TestEquivalenceWithStockDGK:
    def test_same_decisions_as_stock_on_ordinary_telemetry(self, tmp_path):
        rnd = random.Random(3)
        for i in range(120):
            tele = {"latency": rnd.uniform(0, 800), "abort_rate": rnd.uniform(0, 0.5),
                    "reentry_rate": rnd.uniform(0, 4), "load_depth": rnd.uniform(0, 5000),
                    "determinism_index": rnd.uniform(0, 1)}
            stock, st = kernel(tmp_path, f"s{i}")
            gk, g, gkern, gt = governed(tmp_path / f"g{i}" if (tmp_path / f"g{i}").mkdir() is None else tmp_path)
            a = stock.process_transaction("p1", tele, "hi", "ops", st)
            b = gk.process_transaction("p1", tele, "hi", "ops", gt)
            assert a["transaction_status"] == b["transaction_status"], tele
            assert a.get("exception_details") == b.get("exception_details"), tele


class TestGovernedLimits:
    def test_defaults_equal_stock_values(self, tmp_path):
        gk, *_ = governed(tmp_path)
        assert gk.check.limits() == {"latency": 500.0, "abort_rate": 0.25, "reentry_rate": 2.0}

    def test_a_tightened_limit_is_used_on_the_next_transaction(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        assert tx(gk, tok, {"latency": 450.0})["transaction_status"] == "COMMITTED"
        g.propose_tightening("dgk.health.latency", source="ops-tool", new_limit=400.0, reason="test")
        r = tx(gk, tok, {"latency": 450.0})
        assert r["transaction_status"] == "REJECTED"
        if STOCK_HAS_DEFECTS:  # fixed DGK refuses at the door before the check runs
            assert "latency_fault" in r["exception_details"]

    def test_the_limits_cannot_be_raised_through_the_governor_by_a_signal(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        r = g.propose_tightening("dgk.health.latency", source="x", new_limit=900.0)
        assert r["status"] == "rejected" and gk.check.limits()["latency"] == 500.0

    def test_an_operator_can_loosen_and_it_is_audited(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        g.propose_tightening("dgk.health.latency", source="t", new_limit=400.0)
        p = g.proposals.create_proposal(boundary_id="dgk.health.latency", source_evidence=[], current_value=400.0,
                                        proposed_value=500.0, reason="vendor fixed it",
                                        direction=AdaptationDirection.LOOSEN)
        g.submit_for_review(p)
        g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential=OPS)
        assert tx(gk, tok, {"latency": 450.0})["transaction_status"] == "COMMITTED"
        assert g.verify_governance_integrity()[0]

    def test_limits_survive_a_restart(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        g.propose_tightening("dgk.health.abort_rate", source="t", new_limit=0.2)
        g2 = governor(tmp_path)
        k2, t2 = kernel(tmp_path, "k2")
        gk2 = GovernedKernel(k2, g2)
        assert gk2.check.limits()["abort_rate"] == pytest.approx(0.2)
        assert g2.restored["boundaries"] == 3


class TestFailClosedTelemetry:
    @needs_unfixed_dgk
    def test_stock_dgk_commits_a_nan_and_crashes_on_negatives_and_infinities(self, tmp_path):
        """Documents the defects in DGK itself (integrations/DGK_FINDINGS.md). Separate kernels, because
        the NaN corrupts the one it touches."""
        stock, tok = kernel(tmp_path, "nan")
        assert stock.process_transaction("p1", {**CALM, "latency": float("nan")}, "hi", "ops", tok)["transaction_status"] == "COMMITTED"
        for i, bad in enumerate((-1e9, float("inf"))):
            fresh, t2 = kernel(tmp_path, f"bad{i}")
            with pytest.raises(ValueError):
                fresh.process_transaction("p1", {**CALM, "latency": bad}, "hi", "ops", t2)

    @needs_unfixed_dgk
    def test_stock_dgk_commits_small_negative_readings_as_if_valid(self, tmp_path):
        stock, tok = kernel(tmp_path, "neg")
        r = stock.process_transaction("p1", {**CALM, "latency": -600.0}, "hi", "ops", tok)
        assert r["transaction_status"] == "COMMITTED"      # a latency of minus 600 passes a "> 500" limit

    @needs_unfixed_dgk
    def test_one_nan_permanently_corrupts_stock_dgks_statistics(self, tmp_path):
        stock, tok = kernel(tmp_path, "poisoned")
        stock.process_transaction("p1", {**CALM, "latency": float("nan")}, "hi", "ops", tok)
        r = stock.process_transaction("p1", CALM, "hi", "ops", tok)
        assert math.isnan(r["stability_profile"]["lyapunov_energy"])

    def test_the_governed_kernel_keeps_the_statistics_clean(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        tx(gk, tok, {"latency": float("nan")})
        r = tx(gk, tok)
        assert math.isfinite(r["stability_profile"]["lyapunov_energy"])

    @pytest.mark.parametrize("field", ["latency", "abort_rate", "reentry_rate", "load_depth", "determinism_index"])
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0, -1e9])
    def test_governed_refuses_every_such_value_without_raising(self, tmp_path, field, bad):
        gk, g, k, tok = governed(tmp_path)
        r = tx(gk, tok, {field: bad})
        assert r["transaction_status"] == "REJECTED"
        assert r["cause"] == "TELEMETRY_INVALID"

    def test_the_refusal_is_in_dgks_own_audit_trail(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        tx(gk, tok, {"latency": float("nan")})
        assert "TELEMETRY_INVALID" in open(tmp_path / "k.log").read()

    def test_an_unauthorized_caller_still_gets_the_identity_refusal_first(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        r = gk.process_transaction("p1", {**CALM, "latency": float("nan")}, "hi", "ops", "wrong-token")
        assert r["transaction_status"] == "REJECTED" and "not authorized" in r["exception_details"]

    def test_garbage_types_are_refused_not_raised(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        assert tx(gk, tok, {"latency": "fast"})["cause"] == "TELEMETRY_INVALID"


class TestSustainedStressPolicy:
    def test_one_episode_tightens_the_metrics_that_were_near_their_limits_once(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        results = [tx(gk, tok, STRESS) for _ in range(30)]
        fired = [i for i, r in enumerate(results) if "governance" in r]
        assert fired == [9], fired                           # the 10th stressed transaction, once
        statuses = {s["boundary_id"]: s["status"] for s in results[9]["governance"]}
        assert statuses == {"dgk.health.latency": "applied", "dgk.health.abort_rate": "applied",
                            "dgk.health.reentry_rate": "applied"}
        assert gk.check.limits() == pytest.approx({"latency": 450.0, "abort_rate": 0.225, "reentry_rate": 1.8})

    def test_dgks_regime_recovers_by_itself_but_the_limits_stay_tight(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        for _ in range(12):
            tx(gk, tok, STRESS)
        for _ in range(8):
            r = tx(gk, tok, CALM)
        assert r["stability_profile"]["operational_regime"] == "NOMINAL"      # state recovers
        assert gk.check.limits()["latency"] == pytest.approx(450.0)           # limits do not

    def test_nothing_is_signalled_without_sustained_stress(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        for i in range(60):
            r = tx(gk, tok, STRESS if i % 6 == 0 else CALM)
            assert "governance" not in r
        assert gk.check.limits()["latency"] == 500.0

    def test_repeated_episodes_hit_the_circuit_breaker_and_are_held(self, tmp_path):
        policy = SustainedStressPolicy(sustained=5, rearm_after_calm=3)
        gk, g, k, tok = governed(tmp_path, policy=policy)
        last = None
        for _ in range(6):                     # six episodes: stress x6, calm x8
            for _ in range(6):
                r = tx(gk, tok, {**STRESS, "latency": min(STRESS["latency"], gk.check.limits()["latency"])})
                last = r.get("governance", last)
            for _ in range(8):             # DGK needs 3 calm readings to recover, then the policy re-arms
                tx(gk, tok, CALM)
        assert g._auto_tightenings["dgk.health.latency"] == 3
        assert any(s["status"] == "held" for s in gk.signals_sent)
        assert gk.check.limits()["latency"] == pytest.approx(500 * 0.9 ** 3)
        assert len(g.audit.find("tightening_held")) >= 1
        assert g.verify_governance_integrity()[0]

    def test_whatever_the_policy_sees_a_limit_never_goes_up(self, tmp_path):
        rnd = random.Random(11)
        policy = SustainedStressPolicy(sustained=2, rearm_after_calm=1)
        gk, g, k, tok = governed(tmp_path, policy=policy)
        last = gk.check.limits()
        for _ in range(150):
            tele = {"latency": rnd.uniform(0, 600), "abort_rate": rnd.uniform(0, 0.4), "reentry_rate": rnd.uniform(0, 3),
                    "load_depth": rnd.uniform(0, 5000), "determinism_index": rnd.uniform(0, 1)}
            tx(gk, tok, tele)
            now = gk.check.limits()
            assert all(now[a] <= last[a] + 1e-12 for a in now)
            last = now
        assert g.boundaries.unauthorized_loosenings() == []

    def test_signals_are_attributed_in_the_audit_log(self, tmp_path):
        gk, g, k, tok = governed(tmp_path)
        for _ in range(10):
            tx(gk, tok, STRESS)
        entries = [e["payload"] for e in g.audit.find("signal")]
        assert entries and all(e["source"] == "dgk" for e in entries)
        assert entries[0]["evidence"]["regime"] in ("STOCHASTIC_CONFUSION", "ANOMALOUS_DRIFT")


class TestCheckUsedOnItsOwn:
    """GovernedHealthLimitCheck can be dropped into a stock kernel without the wrapper."""

    def test_nan_fails_closed_even_without_the_wrapper(self, tmp_path):
        k, tok = kernel(tmp_path, "solo")
        k.boundary_barrier = GovernedHealthLimitCheck(governor(tmp_path))
        r = k.process_transaction("p1", {**CALM, "latency": float("nan")}, "hi", "ops", tok)
        assert r["transaction_status"] == "REJECTED"
        if STOCK_HAS_DEFECTS:  # fixed DGK refuses at the door before the check runs
            assert "latency_fault" in r["exception_details"]

    def test_limits_are_read_from_the_governor_on_every_call(self, tmp_path):
        k, tok = kernel(tmp_path, "solo2")
        g = governor(tmp_path)
        k.boundary_barrier = GovernedHealthLimitCheck(g)
        assert k.process_transaction("p1", {**CALM, "abort_rate": 0.2}, "hi", "ops", tok)["transaction_status"] == "COMMITTED"
        g.propose_tightening("dgk.health.abort_rate", source="t", new_limit=0.1)
        assert k.process_transaction("p1", {**CALM, "abort_rate": 0.2}, "hi", "ops", tok)["transaction_status"] == "REJECTED"
