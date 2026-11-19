"""Optional "active" trace column (experiments/stack_trace_sim.py): slices with no active task are skipped for that agent."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import stack_trace_sim as sim  # noqa: E402

N, SL = 1000, 250
DEFAULTS = {"deadline_ns": 100_000_000, "tokens_capacity": 4000, "memory_capacity_bytes": 512 * 2**20}


def agent_rows(active=None):
    d = np.full(N, 20_000_000, dtype=np.int64)
    r = {"task_id": np.array([f"t{i}" for i in range(N)], dtype=object), "seq": np.arange(N, dtype=np.int64), "duration_ns": d,
         "tokens": np.full(N, 800, dtype=np.int64), "memory": np.full(N, 10 * 2**20, dtype=np.int64),
         "expected": np.zeros(N, dtype=np.int64), "is_bad": np.zeros(N, dtype=np.int64)}
    if active is not None:
        r["active"] = active
    return r


def test_inactive_slices_are_skipped_and_absent_column_changes_nothing():
    act = np.arange(N) >= 500                                   # idle for the first two slices
    trace = {"fresh": agent_rows(act), "plain": agent_rows()}
    agents = [{"agent_id": a, "profile": "x", "should_tighten": 0, "team": None} for a in trace]
    res = sim.replay(trace, agents, DEFAULTS, {"recommended": sim.POLICIES["recommended"]}, slice_n=SL, tasks_per_agent=N)
    ag = res["policies"]["recommended"]["agents"]
    assert ag["fresh"]["static"]["tasks"] == 500 and ag["fresh"]["governed"]["tasks"] == 500
    assert ag["plain"]["static"]["tasks"] == N and ag["plain"]["governed"]["tasks"] == N
