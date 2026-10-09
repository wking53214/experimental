"""STACK trap events -> governed per-agent limits (integrations/stack_traps.py). Events follow the P3.2 schema."""
import itertools
import math
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


class TestExcessRelease:
    """drift_excess: while drifting, release the hold only if traps exceed what the slowdown explains."""

    MS = 1_000_000

    @staticmethod
    def lognormal_q(median_ms, sigma=0.5, deadline_ms=100.0):
        """Quantiles of COMPLETED tasks: a lognormal truncated at the deadline, as the kernel reports them."""
        from statistics import NormalDist
        nd = NormalDist()
        top = nd.cdf(math.log(deadline_ms / median_ms) / sigma)
        return [median_ms * 1e6 * math.exp(sigma * nd.inv_cdf(p * top)) for p in (0.50, 0.75, 0.90, 0.95, 0.99)]

    def pol(self, **kw):
        return TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True, **kw)

    def slice(self, b, median_ms, traps, tasks=250, agent="agent-7"):
        b.observe_transactions(agent, tasks, completed_median_ns=median_ms * self.MS,
                               completed_quantiles=self.lognormal_q(median_ms))
        return b.ingest([trap(agent=agent, reason="deadline_exceeded") for _ in range(traps)])

    def baseline(self, b):
        for _ in range(5):
            self.slice(b, 20, 0)

    DEF = {**DEFAULTS, "deadline_ns": 100_000_000}

    def bridge(self, tmp_path, policy):
        ops = OperatorRegistry(iterations=1000); ops.register("alice", OPS)
        g = Governor(store_path=str(tmp_path / "ev"), operators=ops)
        return g, TrapBridge(g, defaults=self.DEF, policy=policy)

    def make(self, tmp_path, **kw):
        g, b = self.bridge(tmp_path, self.pol(**kw))
        self.baseline(b)
        return g, b

    def test_the_survival_function_matches_the_lognormal_it_was_built_from(self, tmp_path):
        g, b = self.make(tmp_path)
        assert b._survival("agent-7", 45 * self.MS) == pytest.approx(0.053, abs=0.015)    # P(z > 1.62)
        assert b._survival("agent-7", 100 * self.MS) < 0.005
        xs = [10, 20, 30, 45, 60, 100, 200]
        ys = [b._survival("agent-7", x * self.MS) for x in xs]
        assert all(a >= c for a, c in zip(ys, ys[1:]))

    def test_a_slowdown_that_explains_the_traps_stays_held(self, tmp_path):
        g, b = self.make(tmp_path)                              # 3x slower: explained share at 100 ms about 15%
        for _ in range(6):
            assert self.slice(b, 60, 37) == []                  # 37/250 = 14.8%
        assert b.counts["drift_released"] == 0 and b.counts["drift_held"] > 0

    def test_traps_well_above_the_explained_share_release_the_hold(self, tmp_path):
        g, b = self.make(tmp_path)
        replies = []
        for _ in range(6):
            replies += self.slice(b, 60, 90)                    # 36%: far more than 15% explained
        assert b.counts["drift_released"] > 0
        assert replies and replies[0]["status"] == "applied"

    def test_no_release_when_not_drifting_and_off_by_default(self, tmp_path):
        g, b = self.bridge(tmp_path, TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1))
        self.baseline(b)
        for _ in range(6):
            self.slice(b, 60, 90)
        assert b.counts["drift_released"] == 0                  # drift_excess is off: held throughout
        assert b.limit_for("agent-7", "deadline_exceeded") == 100_000_000.0

    def test_the_window_averages_over_slices(self, tmp_path):
        g, b = self.make(tmp_path)
        self.slice(b, 60, 90)                                   # one noisy slice among calm ones
        for _ in range(3):
            self.slice(b, 60, 37)
        assert b.counts["drift_released"] == 0                 # (90 + 37 x 3) / 1000 = 20% is inside 15% + 7.5%

    @pytest.mark.parametrize("bad", [[1, 2, 3], [5, 4, 3, 2, 1], [0, 1, 2, 3, 4], [float("nan")] * 5, "abc", None, [1, 2, 3, 4, True]])
    def test_malformed_quantiles_are_ignored(self, tmp_path, bad):
        g, b = self.bridge(tmp_path, self.pol())
        for _ in range(6):
            b.observe_transactions("agent-7", 250, completed_median_ns=20 * self.MS, completed_quantiles=bad)
        assert "agent-7" not in b._base_q
        assert b.excess_released("agent-7", 100 * self.MS) is False

    def test_reasons_limits_which_traps_can_tighten(self, tmp_path):
        g, b = make(tmp_path, policy=TrapPolicy(reasons=("deadline_exceeded",)))
        assert b.ingest([trap(reason="token_exhausted") for _ in range(20)]) == []
        assert b.counts["reason_not_enforced"] == 20
        assert b.ingest([trap(reason="deadline_exceeded") for _ in range(5)]) != []

    @pytest.mark.parametrize("true_f", [1.5, 2.0, 3.0])
    def test_the_slowdown_estimate_corrects_the_truncation_bias(self, tmp_path, true_f):
        """Plain median / baseline median underestimates a 3x slowdown (about 2.7x) because killed tasks never complete."""
        g, b = self.make(tmp_path)
        rng = __import__("random").Random(5)
        samples = [20.0 * true_f * math.exp(0.5 * rng.gauss(0, 1)) for _ in range(40000)]
        done = sorted(x for x in samples if x <= 100.0)
        q = [done[int(p * (len(done) - 1))] * 1e6 for p in (0.50, 0.75, 0.90, 0.95, 0.99)]
        assert b._slowdown("agent-7", q, 100 * self.MS) == pytest.approx(true_f, rel=0.08)
