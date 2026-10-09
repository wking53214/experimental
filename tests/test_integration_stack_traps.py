"""STACK trap events -> governed per-agent limits (integrations/stack_traps.py). Events follow the P3.2 schema."""
import itertools
import random
import uuid

import pytest

from integrations.stack_traps import REASON_TO_LIMIT, TrapBridge, TrapPolicy
from src.governance.governor import Governor
from src.governance.operators import OperatorRegistry

DEFAULTS = {"deadline_ns": 50_000_000, "tokens_capacity": 100, "memory_capacity_bytes": 1_048_576}
OPS = "alice-secret-1"


def trap(agent="agent-7", reason="token_exhausted", outcome="RETRY", trap_id=None, **extra):
    return {"trap_id": trap_id or str(uuid.uuid4()), "agent_id": agent, "boundary_layer": 3, "trap_reason": reason,
            "trap_outcome": outcome, "tokens_deficit": 12, **extra}


def make(tmp_path, durable=False, **kw):
    ops = OperatorRegistry(iterations=1000); ops.register("alice", OPS)
    extra = {"audit_path": str(tmp_path / "gov.jsonl")} if durable else {}
    g = Governor(store_path=str(tmp_path / "ev"), operators=ops, **extra)
    return g, TrapBridge(g, defaults=DEFAULTS, **kw)


class TestPattern:
    def test_a_single_trap_changes_nothing_but_prepares_the_agents_boundaries(self, tmp_path):
        g, b = make(tmp_path)
        assert b.ingest([trap()]) == []
        assert b.limit_for("agent-7", "token_exhausted") == 100.0
        assert b.limit_for("agent-7", "deadline_exceeded") == 50_000_000.0

    def test_a_repeated_pattern_tightens_that_agents_matching_limit_only(self, tmp_path):
        g, b = make(tmp_path)
        replies = b.ingest([trap() for _ in range(5)])
        assert [r["status"] for r in replies] == ["applied"]
        assert b.limit_for("agent-7", "token_exhausted") == pytest.approx(90.0)
        assert b.limit_for("agent-7", "memory_exceeded") == 1_048_576.0       # other limits untouched
        b2 = b.limit_for("other-agent", "token_exhausted")
        assert b2 is None                                                    # other agents unaffected / unknown

    @pytest.mark.parametrize("reason", sorted(REASON_TO_LIMIT))
    def test_each_mapped_reason_tightens_its_own_limit(self, tmp_path, reason):
        g, b = make(tmp_path)
        b.ingest([trap(reason=reason) for _ in range(5)])
        assert b.limit_for("agent-7", reason) < DEFAULTS[REASON_TO_LIMIT[reason][0]]

    def test_unmapped_reasons_and_uncounted_outcomes_change_nothing(self, tmp_path):
        g, b = make(tmp_path)
        b.ingest([trap(reason="capability_denied") for _ in range(20)])
        b.ingest([trap(reason="signal_delivered") for _ in range(20)])
        b.ingest([trap(outcome="PASS") for _ in range(20)])
        assert b.counts["unmapped"] == 40 and b.counts["skipped_outcome"] == 20
        assert g.audit.find("signal") == []

    def test_per_event_context_capacity_is_the_starting_limit(self, tmp_path):
        g, b = make(tmp_path)
        b.ingest([trap(context={"tokens_capacity": 400})])
        assert b.limit_for("agent-7", "token_exhausted") == 400.0

    def test_expected_load_is_not_counted(self, tmp_path):
        g, b = make(tmp_path, is_expected=lambda ev: ev["agent_id"] == "nightly-batch")
        b.ingest([trap(agent="nightly-batch") for _ in range(30)])
        assert b.counts["expected"] == 30 and g.audit.find("signal") == []


class TestRobustness:
    def test_replaying_the_same_events_does_not_tighten_again(self, tmp_path):
        g, b = make(tmp_path)
        events = [trap(trap_id=f"t{i}") for i in range(5)]
        b.ingest(events)
        before = b.limit_for("agent-7", "token_exhausted")
        b.ingest(events)                       # at-least-once delivery from the analysis job
        assert b.limit_for("agent-7", "token_exhausted") == before
        # even a fresh bridge replaying the same trap ids is idempotent at the governor
        b2 = TrapBridge(g, defaults=DEFAULTS)
        b2.ingest(events)
        assert b2.limit_for("agent-7", "token_exhausted") == before

    def test_idempotency_survives_a_restart(self, tmp_path):
        g, b = make(tmp_path, durable=True)
        events = [trap(trap_id=f"t{i}") for i in range(5)]
        b.ingest(events)
        g2, b2 = make(tmp_path, durable=True)
        b2.ingest(events)
        assert b2.limit_for("agent-7", "token_exhausted") == pytest.approx(90.0)

    def test_malformed_events_are_skipped_not_raised(self, tmp_path):
        g, b = make(tmp_path)
        junk = [None, 5, "x", [], {}, {"agent_id": 3}, {"agent_id": "a", "trap_reason": 7, "trap_id": "t"},
                {"agent_id": "a", "trap_reason": "token_exhausted"}, {"agent_id": "", "trap_reason": "x", "trap_id": "t"}]
        assert b.ingest(junk) == []
        assert b.counts["skipped_malformed"] == len(junk)

    def test_hostile_agent_ids_cannot_create_unsafe_boundary_names_or_unbounded_boundaries(self, tmp_path):
        g, b = make(tmp_path, max_agents=50)
        weird = ["../../etc/passwd", "a b", "x" * 500, "\u0000", "a\nb", "<script>", "ünï"]
        b.ingest([trap(agent=w, trap_id=f"w{i}") for i, w in enumerate(weird)])
        names = [v.boundary_id for v in g.boundaries.list_boundaries()]
        assert names and all(n.startswith("stack.agent.") and len(n) < 120 for n in names)
        assert all(" " not in n and "/" not in n and "\x00" not in n and "\n" not in n and "<" not in n for n in names)
        assert len({n.split(".")[2] for n in names}) == len(weird)       # every hostile id got its own safe key
        b.ingest([trap(agent=f"a{i}", trap_id=f"n{i}") for i in range(200)])
        assert len(b._agents) == 50 and b.counts["agent_cap"] > 0

    def test_a_flood_from_one_agent_is_bounded_by_the_breaker(self, tmp_path):
        g, b = make(tmp_path)
        replies = b.ingest([trap(trap_id=f"f{i}") for i in range(500)])
        statuses = [r["status"] for r in replies]
        assert statuses.count("applied") == 3 and set(statuses) <= {"applied", "held"}
        assert b.limit_for("agent-7", "token_exhausted") == pytest.approx(100 * 0.9 ** 3)
        assert len(g.audit.find("tightening_held")) == 1
        assert g.verify_governance_integrity()[0]

    def test_a_trap_stream_can_never_raise_any_limit(self, tmp_path):
        rnd = random.Random(5)
        g, b = make(tmp_path, policy=TrapPolicy(min_traps=2, window_events=10))
        reasons = list(REASON_TO_LIMIT) + ["capability_denied", "weird"]
        last = {}
        for i in range(600):
            ev = trap(agent=rnd.choice(["a", "b", "c"]), reason=rnd.choice(reasons),
                      outcome=rnd.choice(["RETRY", "TERMINAL_BREACH", "HALT", "PASS"]), trap_id=f"r{i}")
            b.ingest([ev])
            for v in g.boundaries.list_boundaries():
                assert v.current_limit <= last.get(v.boundary_id, v.current_limit) + 1e-9
                last[v.boundary_id] = v.current_limit
        assert g.boundaries.unauthorized_loosenings() == [] and g.verify_governance_integrity()[0]

    def test_the_bridge_has_no_way_to_loosen_and_a_human_can(self, tmp_path):
        g, b = make(tmp_path)
        b.ingest([trap() for _ in range(5)])
        bid = "stack.agent.agent-7.tokens_capacity"
        r = g.request_loosening(bid, requested_by="stack-sentinel", new_limit=100, reason="agent fixed")
        assert r["status"] == "pending_review" and b.limit_for("agent-7", "token_exhausted") == pytest.approx(90.0)
        g.apply_operator_decision(r["proposal_id"], "approve_loosen", "alice", credential=OPS)
        assert b.limit_for("agent-7", "token_exhausted") == 100.0
        assert g.verify_governance_integrity()[0]

    def test_signals_are_attributed_with_the_trap_evidence(self, tmp_path):
        g, b = make(tmp_path)
        b.ingest([trap(trap_id=f"e{i}", tokens_deficit=7) for i in range(5)])
        e = g.audit.find("signal")[-1]["payload"]
        assert e["source"] == "stack-sentinel" and e["evidence"]["trap_reason"] == "token_exhausted"
        assert e["evidence"]["traps_in_window"] == 5 and e["idempotency_key"] == "trap-e4"


class TestRatePolicy:
    """min_rate: a few traps among very many transactions is a healthy agent, not a pattern."""

    def policy(self, **kw):
        return TrapPolicy(min_rate=0.01, rate_window_tasks=2000, **kw)

    def feed(self, b, traps_per_round, rounds, tasks=500):
        out = []
        for _ in range(rounds):
            b.observe_transactions("agent-7", tasks)
            out += b.ingest([trap() for _ in range(traps_per_round)])
        return out

    def test_a_low_rate_never_tightens_however_long_it_runs(self, tmp_path):
        g, b = make(tmp_path, policy=self.policy())
        assert self.feed(b, 1, 200) == []                      # 0.2% for 100,000 transactions
        assert b.limit_for("agent-7", "token_exhausted") == 100.0
        assert b.counts["below_rate"] > 0

    def test_the_default_policy_tightens_that_same_low_rate_agent(self, tmp_path):
        g, b = make(tmp_path)
        assert self.feed(b, 1, 20) != []                       # why the rate rule exists

    def test_a_high_rate_tightens(self, tmp_path):
        g, b = make(tmp_path, policy=self.policy())
        replies = self.feed(b, 25, 4)                           # 5% of transactions
        assert [r["status"] for r in replies][:1] == ["applied"]
        assert b.limit_for("agent-7", "token_exhausted") == pytest.approx(72.9)   # 100 traps: stopped by the breaker

    def test_without_activity_reports_nothing_tightens(self, tmp_path):
        g, b = make(tmp_path, policy=self.policy())
        assert b.ingest([trap() for _ in range(50)]) == []
        assert b.counts["no_activity"] > 0
        assert b.limit_for("agent-7", "token_exhausted") == 100.0

    def test_the_rate_is_over_the_recent_window_not_all_time(self, tmp_path):
        g, b = make(tmp_path, policy=self.policy())
        b.observe_transactions("agent-7", 100_000)             # a long quiet history
        assert b.ingest([trap() for _ in range(5)]) == []      # 5 traps / 2000 = 0.25%: below 1%
        b.observe_transactions("agent-7", 500)
        replies = b.ingest([trap() for _ in range(20)])        # now 25 / 2000 = 1.25%
        assert replies and replies[0]["status"] == "applied"

    @pytest.mark.parametrize("n", [0, -5, True, "7", None, 2.5])
    def test_bad_activity_reports_are_ignored(self, tmp_path, n):
        g, b = make(tmp_path, policy=self.policy())
        b.observe_transactions("agent-7", n)
        assert b.ingest([trap() for _ in range(50)]) == []


class TestDriftRule:
    """drift_factor: a high trap rate while completed tasks have slowed against the agent's own baseline is a
    regression for a person to look at, not a runaway to tighten."""

    MS = 1_000_000

    def pol(self, **kw):
        return TrapPolicy(min_rate=0.01, drift_factor=1.5, **kw)

    def round(self, b, median_ms, traps=25, tasks=500, agent="agent-7"):
        b.observe_transactions(agent, tasks, completed_median_ns=median_ms * self.MS)
        return b.ingest([trap(agent=agent) for _ in range(traps)])

    def baseline(self, b, agent="agent-7"):
        for _ in range(5):
            self.round(b, 20, traps=0, agent=agent)

    def slow_down(self, b, median_ms=60):
        """Two quiet slow reports: the median of the last three reports now exceeds the baseline."""
        for _ in range(2):
            self.round(b, median_ms, traps=0)

    def test_a_high_rate_with_stable_completions_still_tightens(self, tmp_path):
        g, b = make(tmp_path, policy=self.pol())
        self.baseline(b)
        replies = self.round(b, 21)
        assert replies and replies[0]["status"] == "applied"

    def test_a_high_rate_with_slowed_completions_is_held_not_tightened(self, tmp_path):
        g, b = make(tmp_path, policy=self.pol())
        self.baseline(b)
        self.slow_down(b)
        for _ in range(4):
            assert self.round(b, 60) == []
        assert b.limit_for("agent-7", "token_exhausted") == 100.0
        assert b.counts["drift_held"] > 0

    def test_it_is_recorded_once_per_episode_in_the_audit_log(self, tmp_path):
        g, b = make(tmp_path, policy=self.pol())
        self.baseline(b)
        self.slow_down(b)
        for _ in range(4):
            self.round(b, 60)
        held = [e for e in g.audit.find("signal") if e["payload"].get("kind") == "drift_held"]
        assert len(held) == 1 and held[0]["payload"]["recent_median_ns"] == 60 * self.MS

    def test_off_by_default(self, tmp_path):
        g, b = make(tmp_path, policy=TrapPolicy(min_rate=0.01))
        self.baseline(b)
        assert self.round(b, 60) != []

    def test_an_agent_that_was_slow_from_the_start_has_no_baseline_to_drift_from(self, tmp_path):
        g, b = make(tmp_path, policy=self.pol())
        for _ in range(5):
            self.round(b, 60, traps=0)
        assert self.round(b, 60) != []                         # documented limit: treated as before

    def test_a_limit_already_tightened_gets_a_restore_request_for_a_person(self, tmp_path):
        g, b = make(tmp_path, policy=self.pol())
        self.baseline(b)
        self.round(b, 20)                                       # tightens to 90
        assert b.limit_for("agent-7", "token_exhausted") == pytest.approx(90.0)
        self.slow_down(b)
        for _ in range(3):
            self.round(b, 60)
        pending = g.list_pending_review()
        assert [p.proposed_value for p in pending] == [100.0]
        assert b.limit_for("agent-7", "token_exhausted") == pytest.approx(90.0)   # nothing changed by itself

    def test_it_clears_when_completions_recover(self, tmp_path):
        g, b = make(tmp_path, policy=self.pol())
        self.baseline(b)
        self.slow_down(b)
        for _ in range(3):
            assert self.round(b, 60) == []
        for _ in range(2):
            self.round(b, 20, traps=0)                          # completions recover
        replies = self.round(b, 20)
        assert replies and replies[0]["status"] == "applied"

    @pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf"), True, "20", None])
    def test_bad_medians_are_ignored(self, tmp_path, bad):
        g, b = make(tmp_path, policy=self.pol())
        for _ in range(6):
            b.observe_transactions("agent-7", 500, completed_median_ns=bad)
        assert b.drifting("agent-7") is False and not b._baseline

    def test_a_restart_with_a_drift_entry_in_the_audit_log_still_loads(self, tmp_path):
        g, b = make(tmp_path, durable=True, policy=self.pol())
        self.baseline(b)
        self.slow_down(b)
        for _ in range(3):
            self.round(b, 60)
        assert g.audit.find("signal")
        g2, b2 = make(tmp_path, durable=True, policy=self.pol())
        assert g2.audit.verify()[0]
