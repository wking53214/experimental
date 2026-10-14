"""Regression tests for the red-team findings recorded in docs/RED_TEAM.md (S1 to S5).

Each test is the attack that succeeded before the fix, rewritten to assert that it now fails.
"""
import pytest

from src.governance.grant import is_loosening
from src.governance.governor import Governor
from integrations.stack_traps import TrapBridge, TrapPolicy, _agent_key


DEFAULTS = {"deadline_ns": 100_000_000, "tokens_capacity": 1000, "memory_capacity_bytes": 1 << 30}


def _gov(tmp_path, **kw):
    return Governor(store_path=str(tmp_path / "ev"), max_auto_tightenings=3, **kw)


def _trap(agent, i, reason="deadline_exceeded"):
    return {"trap_id": f"{agent}-{i}", "agent_id": agent, "trap_reason": reason, "trap_outcome": "HALT",
            "boundary_layer": 3, "nanoseconds_since_deadline": 1000}


class TestS1ExactComparison:
    def test_two_ints_that_round_to_the_same_double_are_compared_exactly(self):
        big = 10 ** 18
        assert float(big) == float(big + 50)          # the collision the attack used
        assert is_loosening(big, big + 50)            # a raise, however small, is a loosening
        assert not is_loosening(big + 50, big)
        assert not is_loosening(big, big)

    def test_store_refuses_the_sub_ulp_raise_without_a_grant(self, tmp_path):
        g = _gov(tmp_path, unverified_operators=True)
        g.boundaries.create_boundary("b", "tokens", 10 ** 18)
        with pytest.raises(PermissionError):
            g.boundaries.update_boundary("b", 10 ** 18 + 50, reason="attack")
        assert g.boundaries.get_boundary("b").current_limit == 10 ** 18

    def test_huge_ints_and_infinities_do_not_crash_and_count_as_loosening(self):
        assert is_loosening(10, 10 ** 400)
        assert not is_loosening(10 ** 400, 10)
        assert is_loosening(10, float("inf"))
        assert is_loosening(10, float("-inf"))        # not provably a tightening: refused without a grant
        assert is_loosening(10, float("nan"))
        assert is_loosening(10, True)


class TestS2AcknowledgementNeedsAnOperator:
    def test_without_a_registry_an_acknowledgement_is_refused_by_default(self, tmp_path):
        g = _gov(tmp_path)
        with pytest.raises(PermissionError):
            g.acknowledge_tightening("b", "anyone")
        with pytest.raises(PermissionError):
            g._authenticate_operator("anyone", None)

    def test_breaker_cannot_be_reset_by_an_api_caller(self, tmp_path):
        g = _gov(tmp_path)
        g.boundaries.create_boundary("b", "tokens", 1000)
        applied = 0
        for i in range(12):
            r = g.propose_tightening("b", source="s", factor=0.9, reason="r", idempotency_key=f"k{i}")
            applied += r.get("status") in ("applied", "applied_clamped")
            if r.get("status") == "held":
                with pytest.raises(PermissionError):
                    g.acknowledge_tightening("b", "attacker")
        assert applied == 3

    def test_demo_mode_is_explicit_and_audited(self, tmp_path):
        g = _gov(tmp_path, unverified_operators=True)
        g.acknowledge_tightening("b", "demo")
        last = [e for e in g.audit.entries if e["kind"] == "tightening_acknowledged"][-1]
        assert last["payload"]["identity_verified"] is False


class TestS3AgentCapChurns:
    def test_a_flood_of_ids_does_not_lock_out_a_later_agent(self, tmp_path):
        g = _gov(tmp_path)
        b = TrapBridge(g, defaults=DEFAULTS, policy=TrapPolicy(min_rate=None, min_traps=100), max_agents=50)
        for i in range(200):
            b.ingest([_trap(f"junk{i}", 0)])
        b.ingest([_trap("legit", 0)])
        assert "legit" in b._agents
        assert b.counts["agent_evicted"] > 0 and b.counts["agent_cap"] == 0
        assert len(b._agents) == 50

    def test_a_tightened_agent_is_never_evicted(self, tmp_path):
        g = _gov(tmp_path)
        b = TrapBridge(g, defaults=DEFAULTS, policy=TrapPolicy(min_rate=None, min_traps=3), max_agents=5)
        b.ingest([_trap("victim", i) for i in range(3)])     # tightened once
        assert b.boundary_id("victim", "deadline_ns") in b._tightened_boundaries
        for i in range(100):
            b.ingest([_trap(f"junk{i}", 0)])
        assert "victim" in b._agents
        assert len(b._agents) == 5

    def test_when_every_agent_is_tightened_the_cap_holds(self, tmp_path):
        g = _gov(tmp_path)
        b = TrapBridge(g, defaults=DEFAULTS, policy=TrapPolicy(min_rate=None, min_traps=3), max_agents=3)
        for a in ("a", "b", "c"):
            b.ingest([_trap(a, i) for i in range(3)])
        b.ingest([_trap("d", 0)])
        assert "d" not in b._agents and b.counts["agent_cap"] == 1


class TestS4ReservedPrefix:
    def test_an_ascii_id_spelled_like_a_hashed_key_does_not_collide(self):
        victim = "агент-7"
        hashed = _agent_key(victim)
        assert hashed.startswith("h-")
        assert _agent_key(hashed) != hashed
        assert _agent_key(hashed).startswith("h-")

    def test_plain_ids_are_unchanged(self):
        assert _agent_key("agent-1") == "agent-1"
