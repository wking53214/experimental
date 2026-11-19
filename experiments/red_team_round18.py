#!/usr/bin/env python3
"""Round 18: try to break the certificate and the cost budget (docs/CERTIFICATE.md). Protocol in docs/TRACE_TEST_PLAN.md, round 18.

Hand-built agents, 8000 tasks (32 slices), deadline 100 ms, nominal tokens and memory:
  stepchange-honest  honest; median 20 ms, then 50 ms from task 3000 (a step inside one slice); no hangs
  bandstack-honest   honest worst case: 98.5% of tasks in the top 27.1% band (76 to 82 ms), 1.5% natural overruns
  floorpump-attacker completes slowly (90 to 99 ms) for 6 slices, then hangs 5% of tasks, to push its own budget floor up
  sybil-k (k=0..5)   one attacker that changes identity every 6 slices; each identity is idle outside its window
Policies: recommended, recommended_budget, recommended_slice, recommended_slice_budget. 20 seeds from 18000.

usage: python experiments/red_team_round18.py [--seeds 20] [--out experiments/red_team_round18.json]
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
F = 0.9 ** 3
SEED_BASE = 18000


def arrays(dur_ns, bad):
    n = len(dur_ns)
    return {"task_id": np.array([f"t{i}" for i in range(n)], dtype=object), "seq": np.arange(n, dtype=np.int64),
            "duration_ns": np.maximum(1, dur_ns).astype(np.int64), "tokens": np.full(n, 800, dtype=np.int64),
            "memory": np.full(n, 120 * 2**20, dtype=np.int64), "expected": np.zeros(n, dtype=np.int64),
            "is_bad": bad.astype(np.int64)}


def build(seed):
    rng = np.random.default_rng(np.random.SeedSequence(seed))
    seq = np.arange(N)
    trace, agents = {}, []
    # stepchange-honest
    med = np.where(seq >= 3000, 50.0, 20.0)
    d = rng.lognormal(0.0, 0.5, N) * med * MS
    trace["stepchange-honest"] = arrays(d, np.zeros(N)); agents.append(("stepchange-honest", 0))
    # bandstack-honest
    d = rng.uniform(76, 82, N) * MS
    over = rng.random(N) < 0.015
    d[over] = rng.uniform(120, 200, over.sum()) * MS
    trace["bandstack-honest"] = arrays(d, np.zeros(N)); agents.append(("bandstack-honest", 0))
    # floorpump-attacker
    d = rng.uniform(90, 99, N) * MS
    hang = (rng.random(N) < 0.05) & (seq >= 6 * SLICE)
    d[hang] = rng.uniform(300, 900, hang.sum()) * MS
    trace["floorpump-attacker"] = arrays(d, hang); agents.append(("floorpump-attacker", 1))
    # sybil: one attacker, a new identity every 6 slices
    for k in range(6):
        lo, hi = 6 * k * SLICE, min(N, 6 * (k + 1) * SLICE)
        active = (seq >= lo) & (seq < hi)
        d = np.full(N, 1.0 * MS)
        d[active] = rng.uniform(20, 60, active.sum()) * MS
        hang = active & (rng.random(N) < 0.05)
        d[hang] = rng.uniform(300, 900, hang.sum()) * MS
        trace[f"sybil-{k}"] = arrays(d, hang); agents.append((f"sybil-{k}", 1))
    return trace, [{"agent_id": a, "profile": a.split("-")[0], "should_tighten": st, "team": None} for a, st in agents]


def job(seed):
    ms.TASKS = N
    trace, agents = build(seed)
    defaults = {"deadline_ns": D, "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in POLICIES}, slice_n=SLICE, tasks_per_agent=N)
    rows = []
    for pol, pr in res["policies"].items():
        for a, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            t = trace[a]; legit = t["is_bad"] == 0; dd = t["duration_ns"][legit]
            active = dd > 2 * MS if a.startswith("sybil") else np.ones(len(dd), bool)
            B = 100.0 * float(((dd > F * D) & (dd <= D) & active).sum()) / max(1, int(active.sum()))
            rows.append({"seed": seed, "policy": pol, "agent": a, "should_tighten": v["should_tighten"],
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, int(active.sum())),
                         "bound_points": B, "bad_ns_static": s["bad_ns"], "bad_ns_governed": g["bad_ns"],
                         "final_deadline_ratio": (v["limits"]["deadline_ns"]["limit"] if v["limits"]["deadline_ns"] else D) / D})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "red_team_round18.json"))
    a = ap.parse_args()
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, [SEED_BASE + i for i in range(a.seeds)]):
            rows += r
    json.dump({"seed_base": SEED_BASE, "rows": rows}, open(a.out, "w"), indent=1)
    print(len(rows), "rows written")


if __name__ == "__main__":
    main()
