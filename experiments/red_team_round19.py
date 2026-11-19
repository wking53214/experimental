#!/usr/bin/env python3
"""Round 19: the fresh-identity (sybil) test round 18 could not run. Agents exist only from their first task: the trace carries an
"active" column and idle slices are skipped, so a new identity has no history. Protocol: docs/TRACE_TEST_PLAN.md, round 19.

  persistent       one identity for all 32 slices; fast tasks (20 to 60 ms), 5% hangs (control)
  sybil6-k         the same behavior, a new identity every 6 slices (k = 0..5; the last window is 2 slices)
  sybil3-k         a new identity every 3 slices (k = 0..10; the last window is 2 slices)
Deadline 100 ms, 8000 tasks per agent, 20 seeds from 19000, policies as round 18.

usage: python experiments/red_team_round19.py [--seeds 20] [--out experiments/red_team_round19.json]
"""
import argparse, json, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import stack_trace_multiseed as ms   # noqa: E402
import gen_trace                     # noqa: E402

POLICIES = ("recommended", "recommended_budget", "recommended_slice", "recommended_slice_budget")
N, SLICE, D = 8000, 250, gen_trace.DEADLINE_NS
MS = 1_000_000
SEED_BASE = 19000


def make(rng, active):
    d = rng.uniform(20, 60, N) * MS
    hang = active & (rng.random(N) < 0.05)
    d[hang] = rng.uniform(300, 900, hang.sum()) * MS
    return {"task_id": np.array([f"t{i}" for i in range(N)], dtype=object), "seq": np.arange(N, dtype=np.int64),
            "duration_ns": d.astype(np.int64), "tokens": np.full(N, 800, dtype=np.int64),
            "memory": np.full(N, 120 * 2**20, dtype=np.int64), "expected": np.zeros(N, dtype=np.int64),
            "is_bad": hang.astype(np.int64), "active": active}


def build(seed):
    rng = np.random.default_rng(np.random.SeedSequence(seed))
    seq = np.arange(N)
    trace = {"persistent": make(rng, np.ones(N, bool))}
    for tag, per in (("sybil6", 6), ("sybil3", 3)):
        k = 0
        while per * k * SLICE < N:
            lo, hi = per * k * SLICE, min(N, per * (k + 1) * SLICE)
            trace[f"{tag}-{k}"] = make(rng, (seq >= lo) & (seq < hi)); k += 1
    return trace, [{"agent_id": a, "profile": a.split("-")[0], "should_tighten": 1, "team": None} for a in trace]


def job(seed):
    ms.TASKS = N
    trace, agents = build(seed)
    defaults = {"deadline_ns": D, "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in POLICIES}, slice_n=SLICE, tasks_per_agent=N)
    return [{"seed": seed, "policy": pol, "agent": a, "group": a.split("-")[0], "bad_ns_static": v["static"]["bad_ns"],
             "bad_ns_governed": v["governed"]["bad_ns"], "first_tightened_slice": v["first_tightened_slice"]}
            for pol, pr in res["policies"].items() for a, v in pr["agents"].items()]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "red_team_round19.json"))
    a = ap.parse_args()
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, [SEED_BASE + i for i in range(a.seeds)]):
            rows += r
    json.dump({"seed_base": SEED_BASE, "rows": rows}, open(a.out, "w"), indent=1)
    print(len(rows), "rows written")


if __name__ == "__main__":
    main()
