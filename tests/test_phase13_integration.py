"""Phase 13: the controls working together under the default configuration.

Each earlier test file exercises one control. Here the defaults (generative detection, breaker,
semantic layer) run with an operator registry and an audit log, under attack.
"""
import numpy as np
import pytest

from src.governance.audit_log import _entry_hash, GENESIS
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry
from src.governance.proposal import AdaptationDirection
from src.governance.workload import ExpectedLoadPattern, WorkloadType

ALICE, BOB = "alice-secret-1", "bob-secret-22"


def registry():
    r = OperatorRegistry(iterations=1000)
    r.register("alice", ALICE)
    r.register("bob", BOB)
    return r


def pattern(pid="p1", lo=100.0, hi=200.0, boundary="api"):
    return ExpectedLoadPattern(pattern_id=pid, boundary_id=boundary, resource_or_action="rate",
                               workload_type=WorkloadType.BATCH_JOB, description="nightly batch",
                               expected_value_range=(lo, hi), expected_duration_seconds=60.0,
                               schedule="nightly", severity="low")


def flood(g, n=40, bid="api", factor=1.05):
    """An observation-only attacker: values just over the current limit."""
    applied = 0
    for _ in range(n):
        lim = g.boundaries.get_boundary(bid).current_limit
        g.execute_against_boundary(bid, lim * factor)
        p = g.detect_and_propose_adaptation(bid)
        if p:
            a, _ = g.authorize_proposal(p)
            g.apply_approved_proposal(a)
            applied += 1
    return applied


def make(tmp_path, **kw):
    g = Governor(store_path=str(tmp_path), operators=registry(), **kw)
    g.boundaries.create_boundary("api", "rate", 100.0)
    return g


class TestT4ExpectedLoadSpoofing:
    def _violations_then_propose(self, g, value=150.0):
        for _ in range(5):
            g.execute_against_boundary("api", value)
        return g.detect_and_propose_adaptation("api")

    def test_default_is_open_to_a_spoofed_pattern(self, tmp_path):
        """Documents the hole: with default settings anyone who can call the classifier can
        excuse their own attack."""
        g = make(tmp_path)
        g.classifier.register_expected_pattern(pattern())  # attacker, no credential
        assert self._violations_then_propose(g) is None, "attack was excused"

    def test_strict_patterns_ignore_a_direct_registration(self, tmp_path):
        g = make(tmp_path, strict_patterns=True)
        g.classifier.register_expected_pattern(pattern())
        assert "p1" in g.classifier.unapproved
        assert self._violations_then_propose(g) is not None, "attack must still count"

    def test_strict_patterns_honor_an_authenticated_operator(self, tmp_path):
        g = make(tmp_path, strict_patterns=True)
        g.register_expected_pattern(pattern(), "alice", ALICE, "nightly batch is expected")
        assert self._violations_then_propose(g) is None
        entry = g.audit.find("pattern_registered")[-1]["payload"]
        assert entry["operator_id"] == "alice" and entry["identity_verified"] is True

    def test_registering_a_pattern_needs_the_operators_own_credential(self, tmp_path):
        g = make(tmp_path, strict_patterns=True)
        for who, cred in (("alice", None), ("alice", BOB), ("mallory", ALICE)):
            with pytest.raises(PermissionError):
                g.register_expected_pattern(pattern(), who, cred)
        assert g.classifier.expected_patterns == {}
        assert g.audit.find("operator_auth_failed")

    def test_pattern_registration_is_blocked_without_the_semantic_layer(self, tmp_path):
        g = Governor(store_path=str(tmp_path), use_semantic=False)
        with pytest.raises(ValueError):
            g.register_expected_pattern(pattern(), "alice")


class TestIntegratedLifecycle:
    def test_flood_is_bounded_by_the_default_breaker_and_stays_auditable(self, tmp_path):
        g = make(tmp_path)
        assert g.max_auto_tightenings == 3 and g.detection == "generative"
        assert flood(g, 60) == 3
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(72.9)
        assert g.tightening_holds and g.audit.find("tightening_held")
        ok, checks = g.verify_governance_integrity()
        assert ok, checks

    def test_impersonation_cannot_loosen_but_the_real_operator_can(self, tmp_path):
        g = make(tmp_path)
        flood(g, 60)
        p = g.proposals.create_proposal(
            boundary_id="api", source_evidence=[], current_value=72.9, proposed_value=100.0,
            reason="restore", direction=AdaptationDirection.LOOSEN)
        g.submit_for_review(p)
        for who, cred in (("alice", "guess"), ("alice", BOB), ("mallory", ALICE), ("alice", None)):
            with pytest.raises(PermissionError):
                g.apply_operator_decision(p.proposal_id, "approve_loosen", who, credential=cred)
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(72.9)
        g.apply_operator_decision(p.proposal_id, "approve_loosen", "alice", credential=ALICE)
        assert g.boundaries.get_boundary("api").current_limit == 100.0
        ok, checks = g.verify_governance_integrity()
        assert ok, checks

    def test_acknowledgement_needs_credentials_and_reopens_tightening(self, tmp_path):
        g = make(tmp_path)
        flood(g, 60)
        with pytest.raises(PermissionError):
            g.acknowledge_tightening("api", "alice", credential="nope")
        assert flood(g, 60) == 0  # still held
        g.acknowledge_tightening("api", "alice", credential=ALICE)
        assert flood(g, 60) == 3  # three more, then held again
        assert g.boundaries.get_boundary("api").current_limit == pytest.approx(100 * 0.9 ** 6)
        assert g.verify_governance_integrity()[0]

    def test_tampering_during_the_run_is_caught_at_each_layer(self, tmp_path):
        g = make(tmp_path)
        flood(g, 60)
        anchor = g.audit_anchor()
        assert g.verify_governance_integrity()[0]

        # 1. rewrite a recorded decision in memory
        g.authority.decisions[0].decided_by = "alice"
        assert not g.verify_governance_integrity()[0]
        g.authority.decisions[0].decided_by = "system"
        assert g.verify_governance_integrity()[0]

        # 2. raise a limit directly in the version history, bypassing the authority
        hist = g.boundaries.boundaries["api"]
        last = hist.versions[max(hist.versions)]
        last.current_limit = 500.0
        assert not g.verify_governance_integrity()[0]
        last.current_limit = 72.9
        assert g.verify_governance_integrity()[0]

        # 3. edit a log entry
        g.audit.entries[1]["payload"]["reason"] = "forged"
        assert not g.verify_governance_integrity()[0]

        # 4. rewrite the whole log consistently: passes alone, fails against the anchor
        prev = GENESIS
        for i, e in enumerate(g.audit.entries):
            e["seq"], e["prev"] = i, prev
            e["hash"] = _entry_hash(prev, i, e["ts"], e["kind"], e["payload"])
            prev = e["hash"]
        assert g.audit.verify()[0] and not g.audit.verify(anchor)[0]


class TestDefaultDetectionOnManyMetrics:
    names = [f"m{i}" for i in range(30)]

    def _run(self, tmp_path, seed, steps=1500, **kw):
        g = Governor(store_path=str(tmp_path), **kw)
        g.boundaries.create_boundary("svc", "cpu", 100.0)
        rng = np.random.default_rng(seed)
        alarms = tight = 0
        for t in range(steps):
            det, _ = g.ingest_metrics("svc", float(t), dict(zip(self.names, map(float, rng.standard_normal(30)))))
            alarms += bool(det["anomaly_detected"])
            p = g.detect_and_propose_adaptation("svc")
            if p:
                a, _ = g.authorize_proposal(p)
                g.apply_approved_proposal(a)
                tight += 1
        return g, alarms, tight

    def test_generative_default_alarm_rate_on_clean_many_metric_data_is_low(self, tmp_path):
        """The hybrid default recorded a violation on almost every step of real many-metric
        telemetry. Before the warmup fix the generative default alarmed on 48% of the first 100
        steps at 30 metrics."""
        _, alarms, _ = self._run(tmp_path, 0)
        assert alarms / 1500 < 0.02

    def test_known_leak_sparse_false_alarms_still_tighten_under_default_settings(self, tmp_path):
        """Documents a defect of the defaults: evidence never expires, so a handful of false alarms
        spread over 1500 clean steps add up to the breaker cap. Not fixed by default (see
        docs/PHASE_13_REPORT.md); the next test shows the opt-in fix."""
        for seed in range(3):
            g, alarms, tight = self._run(tmp_path / f"d{seed}", seed)
            assert alarms < 40 and tight == 3
            assert g.boundaries.get_boundary("svc").current_limit == pytest.approx(72.9)

    def test_evidence_window_plus_fresh_evidence_stops_the_leak(self, tmp_path):
        total = 0
        for seed in range(6):
            g, _, tight = self._run(tmp_path / f"w{seed}", seed, 3000, evidence_window=100,
                                    require_fresh_evidence=True)
            total += tight
            assert tight <= 1
            assert g.verify_governance_integrity()[0]
        assert total <= 6  # was 3 per run (18) with the defaults

    def test_the_window_still_lets_a_real_flood_through(self, tmp_path):
        g = make(tmp_path, evidence_window=100, require_fresh_evidence=True)
        assert flood(g, 60) == 3  # breaker cap reached: three violations in 100 steps is a pattern


class TestHoldLogging:
    def test_a_hold_is_recorded_once_per_episode_not_once_per_step(self, tmp_path):
        g = make(tmp_path)
        flood(g, 200)  # trips the breaker early, then 190+ more held steps
        assert len(g.tightening_holds) == 1
        assert len(g.audit.find("tightening_held")) == 1
        g.acknowledge_tightening("api", "alice", credential=ALICE)
        flood(g, 200)  # trips again: a new episode, a second record
        assert len(g.tightening_holds) == 2
        assert len(g.audit.find("tightening_held")) == 2
        assert g.verify_governance_integrity()[0]


class TestKnownGapRestart:
    def test_known_gap_a_restart_resets_tightened_limits(self, tmp_path):
        """Documents threat T13: boundaries are held in memory only, so a restart discards the
        tightening and the application re-creates the boundary at its configured value. That is a
        loosening with no grant and no audit record. Not fixed yet: when it is, this test must be
        rewritten to assert the limit survives."""
        g = Governor(store_path=str(tmp_path))
        g.boundaries.create_boundary("api", "rate", 100.0)
        flood(g, 30)
        assert g.boundaries.get_boundary("api").current_limit < 100.0
        g2 = Governor(store_path=str(tmp_path))  # a restart with the same store path
        with pytest.raises(KeyError):
            g2.boundaries.get_boundary("api")
        g2.boundaries.create_boundary("api", "rate", 100.0)
        assert g2.boundaries.get_boundary("api").current_limit == 100.0
        assert g2.verify_governance_integrity()[0], "and nothing notices"
