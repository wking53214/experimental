"""The Python model of the kernel (experiments/stack_trace_sim.py) must reproduce the Rust kernel's committed replay
results: identical static totals and identical tightening decisions on all three committed traces.

Known, accepted difference: in the committed v1 run the kernel example did not act on a tightened MEMORY cap (the leak
agents' memory traps stayed at their static count after the cap came down), while the model applies whatever limit
the governor holds. Only the leak agents' memory_traps and legit_failed under memory-tightening policies differ, and
the recommended policy never tightens memory. That is excluded below and recorded in docs/TRACE_TEST_PLAN.md.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
import stack_trace_sim as sim  # noqa: E402

TOTAL_KEYS = ("tasks", "legit", "legit_failed", "expected_trapped", "bad", "bad_caught", "deadline_traps",
              "token_traps", "memory_traps", "bad_ns", "total_ns")
CASES = {
    "inflate_v1": "stack_trace_replay_results_inflate_v1.json",
    "holdout_v1": "stack_trace_replay_results_holdout_v1.json",
    "v1": "stack_trace_replay_results.json",
}


def _run(tag):
    csv_path = ROOT / "testdata" / "traces" / f"stack_trace_{tag}.csv"
    _tag, manifest, agents = sim.load_set(csv_path)
    trace = sim.load_trace(csv_path)
    ref = json.loads((ROOT / "experiments" / CASES[tag]).read_text())
    got = sim.replay(trace, agents, manifest["defaults"], {p: sim.POLICIES[p] for p in ref["policies"]},
                     slice_n=ref["slice_tasks"], tasks_per_agent=manifest["tasks_per_agent"])
    assert got["slices"] == ref["slices"]
    return ref, got


def _memory_exception(policy, agent, key, ref_agent):
    """The kernel example ignored a tightened memory cap: see the module docstring."""
    mem = ref_agent["limits"]["memory_capacity_bytes"]
    tightened_memory = mem is not None and mem["limit"] < 536870912.0
    return tightened_memory and agent.startswith("leak-") and key in ("memory_traps", "legit_failed")


@pytest.mark.parametrize("tag", sorted(CASES))
def test_model_matches_the_kernel_replay(tag):
    ref, got = _run(tag)
    first = next(iter(ref["policies"]))
    for pname, pr in ref["policies"].items():
        gp = got["policies"][pname]
        assert gp["audit_chain_intact"] and pr["audit_chain_intact"]
        for agent, v in pr["agents"].items():
            w = gp["agents"][agent]
            if pname == first:
                assert w["static"] == {k: v["static"][k] for k in TOTAL_KEYS}, (tag, agent, "static")
            assert (w["tightened"], w["first_tightened_slice"]) == (v["tightened"], v["first_tightened_slice"]), (tag, pname, agent)
            for x, lim in v["limits"].items():
                assert (w["limits"][x] and w["limits"][x]["limit"]) == (lim and lim["limit"]), (tag, pname, agent, x)
            for k in TOTAL_KEYS:
                if _memory_exception(pname, agent, k, v):
                    continue
                assert w["governed"][k] == v["governed"][k], (tag, pname, agent, k)


def test_the_slowdown_cache_does_not_change_results(tmp_path):
    """The memo in TrapBridge._slowdown is keyed on everything the estimate depends on."""
    from integrations.stack_traps import TrapBridge, TrapPolicy
    from src.governance.governor import Governor
    g = Governor(store_path=str(tmp_path / "s"), use_semantic=True)
    b = TrapBridge(g, defaults={"deadline_ns": 100e6, "tokens_capacity": 4000, "memory_capacity_bytes": 2**29},
                   policy=TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_excess=True))
    q = [20e6, 28e6, 38e6, 46e6, 70e6]
    for _ in range(5):
        b.observe_transactions("a", 250, completed_median_ns=20e6, completed_quantiles=q)
    f1 = b._slowdown("a", [60e6, 84e6, 95e6, 98e6, 99.5e6], 100e6)
    b._slow_cache.clear()
    f2 = b._slowdown("a", [60e6, 84e6, 95e6, 98e6, 99.5e6], 100e6)
    assert f1 == f2 and f1 > 1.5
    assert b._slowdown("a", [60e6, 84e6, 95e6, 98e6, 99.5e6], 80e6) != f1   # the deadline is part of the key


def test_keyed_canary_selection_matches_the_reference_hash_and_is_unpredictable_by_seq():
    """The kernel example uses the same 64-bit FNV-1a; these vectors pin the contract (T16)."""
    import numpy as np
    assert sim.fnv1a64(b"") == 0xcbf29ce484222325
    assert sim.fnv1a64(b"a") == 0xaf63dc4c8601ec8c
    assert sim.fnv1a64(b"k-7f3a|agent-1|42") == 0xca8a5faaefe020a3
    seq = np.arange(10_000)
    plain = sim.canary_mask(seq, 10, "agent-1")
    keyed = sim.canary_mask(seq, 10, "agent-1", key="k-7f3a")
    assert plain.sum() == 1000 and bool(plain[0]) and bool(plain[10]) and not plain[1]
    assert 850 <= keyed.sum() <= 1150                                   # about a tenth, by hash
    overlap = (plain & keyed).sum()
    assert 60 <= overlap <= 140                                         # independent of seq % 10
    other = sim.canary_mask(seq, 10, "agent-2", key="k-7f3a")
    assert (keyed != other).sum() > 1000                                # and of the agent


def test_canary_plan_carries_the_key_only_when_set(tmp_path):
    from integrations.stack_traps import TrapBridge, TrapPolicy
    from src.governance.governor import Governor
    defaults = {"deadline_ns": 100e6, "tokens_capacity": 4000, "memory_capacity_bytes": 2**29}
    for key in (None, "k-7f3a"):
        g = Governor(store_path=str(tmp_path / f"s{key}"))
        b = TrapBridge(g, defaults=defaults, policy=TrapPolicy(min_rate=0.01, reasons=("deadline_exceeded",),
                                                                validate_after=4, canary_key=key))
        b.observe_transactions("a", 500)
        b.ingest([{"trap_id": f"t{i}", "agent_id": "a", "trap_reason": "deadline_exceeded", "trap_outcome": "HALT"} for i in range(25)])
        plan = b.canary_plan("a")
        assert plan["share"] == 0.10 and plan["deadline_ns"] == 100e6
        assert plan.get("key") == key
