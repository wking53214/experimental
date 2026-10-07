"""
Attacks on the core invariant: no automatic path may raise a limit.

TestObservationOnlyAttacker: the threat model the thesis targets (an attacker who
controls only the observations fed to the governor). Must hold.

TestApiBypasses: ways a bug or misuse of the public API can raise a limit without a
human, and the integrity check failing to notice. Each is a known gap, marked as a
strict expected failure: when one is fixed the test will start passing and strict
mode forces the marker to be removed.
"""
import random

import pytest

from src.governance.authority import AuthorizationResult
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection


def _gov(tmp_path, limit=100, semantic=True):
    g = Governor(store_path=str(tmp_path), use_semantic=semantic)
    g.boundaries.create_boundary("b", "cpu", limit)
    return g


def _limit(g):
    return g.boundaries.get_boundary("b").current_limit


class TestObservationOnlyAttacker:
    WEIRD = [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, 1e308, -1e308, 1e-308, 5e-324]

    def test_fuzzed_observations_never_raise_a_limit(self, tmp_path):
        rng = random.Random(0)
        for ep in range(60):
            g = Governor(store_path=str(tmp_path / f"ep{ep}"), use_semantic=bool(ep % 2))
            start = rng.choice([1, 10, 100, 1000, 1e6])
            g.boundaries.create_boundary("b", "r", start)
            g.patterns.create_pattern("b_p", "b", 2, 30)
            prev = start
            for _ in range(50):
                cur = _limit(g)
                v = rng.choice(self.WEIRD) if rng.random() < 0.2 else cur * rng.uniform(0.0, 3.0)
                try:
                    _, viol = g.execute_against_boundary("b", v)
                    if viol:
                        prop = g.detect_and_propose_adaptation("b")
                        if prop:
                            _, res = g.authorize_proposal(prop)
                            if res == AuthorizationResult.AUTO_APPROVED:
                                g.apply_approved_proposal(prop)
                except Exception:
                    pass
                now = _limit(g)
                assert now <= prev, "limit was raised by observations alone"
                assert now >= start * 0.2 - 1e-9, "limit fell below the 20% usability floor"
                prev = now
            assert g.authority.verify_no_auto_loosen()


class TestApiBypasses:
    @pytest.mark.xfail(strict=True, reason="authority trusts the declared direction; "
                       "a proposal labelled TIGHTEN with a larger value is auto-approved and applied")
    def test_mislabelled_tighten_cannot_raise_limit(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.TIGHTEN)
        g.authorize_proposal(p)
        try:
            g.apply_approved_proposal(p)
        except Exception:
            pass
        assert _limit(g) <= before

    @pytest.mark.xfail(strict=True, reason="BoundaryRegistry.update_boundary has no direction guard")
    def test_update_boundary_cannot_raise_limit_directly(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        try:
            g.boundaries.update_boundary("b", before * 10)
        except Exception:
            pass
        assert _limit(g) <= before

    @pytest.mark.xfail(strict=True, reason="ProposalStore.mark_approved needs no authority decision, "
                       "so a LOOSEN proposal can be approved and applied without a human")
    def test_loosen_cannot_be_approved_without_a_human(self, tmp_path):
        g = _gov(tmp_path)
        before = _limit(g)
        p = g.proposals.create_proposal("b", [], before, before * 2, "x", AdaptationDirection.LOOSEN)
        g.authorize_proposal(p)
        g.proposals.mark_approved(p.proposal_id)
        try:
            g.apply_approved_proposal(p)
        except Exception:
            pass
        assert _limit(g) <= before

    @pytest.mark.xfail(strict=True, reason="verify_governance_integrity inspects only the authority's own "
                       "decision log, so an actual limit increase is not detected")
    def test_integrity_check_detects_an_actual_limit_increase(self, tmp_path):
        g = _gov(tmp_path)
        g.boundaries.update_boundary("b", _limit(g) * 10)
        ok, _ = g.verify_governance_integrity()
        assert ok is False
