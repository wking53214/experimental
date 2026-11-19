#!/usr/bin/env python3
"""Round 20: a provisional one-step floor for a new identity (TrapPolicy.cost_budget_new_identity_ratio). Protocol: docs/TRACE_TEST_PLAN.md, round 20.

  sim  : round 19's attackers (persistent, sybil6-k, sybil3-k) plus three honest newcomers (fresh identity at slice 10, natural 2.3% tail),
         20 seeds from 20000, six policies (the four of round 18 plus the two *_prov policies)
  real : the round 17 hold-out stretches (H1, H2) at 10, 60 and 180 s, policies recommended, recommended_budget, recommended_budget_prov

usage: python experiments/red_team_round20.py --data PATH/syfi_coding_trace.jsonl.gz [--seeds 20]
"""
import argparse, json, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import stack_trace_multiseed as ms   # noqa: E402
import gen_trace                     # noqa: E402
import red_team_round19 as r19       # noqa: E402
import real_trace_round16 as r16     # noqa: E402
import real_trace_round17 as r17     # noqa: E402

SIM_POLICIES = ("recommended", "recommended_budget", "recommended_budget_prov",
                "recommended_slice", "recommended_slice_budget", "recommended_slice_budget_prov")
REAL_POLICIES = ("recommended", "recommended_budget", "recommended_budget_prov")
N, SLICE, D, MS = r19.N, r19.SLICE, r19.D, r19.MS
SEED_BASE = 20000


def build(seed):
    trace, agents = r19.build(seed)
    rng = np.random.default_rng(np.random.SeedSequence([seed, 7]))
    seq = np.arange(N)
    for k in range(3):
        d = rng.lognormal(0.0, 0.6, N) * 30 * MS
        trace[f"newcomer-{k}"] = {"task_id": np.array([f"t{i}" for i in range(N)], dtype=object), "seq": seq.astype(np.int64),
                                  "duration_ns": d.astype(np.int64), "tokens": np.full(N, 800, dtype=np.int64),
                                  "memory": np.full(N, 120 * 2**20, dtype=np.int64), "expected": np.zeros(N, dtype=np.int64),
                                  "is_bad": np.zeros(N, dtype=np.int64), "active": seq >= 10 * SLICE}
        agents.append({"agent_id": f"newcomer-{k}", "profile": "newcomer", "should_tighten": 0, "team": None})
    return trace, agents


def sim_job(seed):
    ms.TASKS = N
    trace, agents = build(seed)
    defaults = {"deadline_ns": D, "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in SIM_POLICIES}, slice_n=SLICE, tasks_per_agent=N)
    rows = []
    for pol, pr in res["policies"].items():
        for a, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            d = trace[a]["duration_ns"][trace[a]["active"]]
            rows.append({"seed": seed, "policy": pol, "agent": a, "group": a.split("-")[0], "should_tighten": v["should_tighten"],
                         "bad_ns_static": s["bad_ns"], "bad_ns_governed": g["bad_ns"],
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]),
                         "bound_points": 100.0 * float(((d > r16.F * D) & (d <= D)).mean()) if not v["should_tighten"] else None})
    return rows


def real_job(args):
    pop, dl_s, lat = args
    mn, start, use = r17.REAL_POPS[pop]
    trace, agents = r17.build(lat, mn, start, use)
    Dr = int(dl_s * 1e9)
    defaults = {"deadline_ns": Dr, "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in REAL_POLICIES}, slice_n=SLICE, tasks_per_agent=use)
    rows = []
    for pol, pr in res["policies"].items():
        for u, v in pr["agents"].items():
            s, g = v["static"], v["governed"]; d = trace[u]["duration_ns"]
            rows.append({"pop": pop, "deadline_s": dl_s, "policy": pol, "agent": u,
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]),
                         "bound_points": 100.0 * float(((d > r16.F * Dr) & (d <= Dr)).mean())})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "red_team_round20.json"))
    a = ap.parse_args()
    lat = r16.load(a.data)
    sim, real = [], []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(sim_job, [SEED_BASE + i for i in range(a.seeds)]):
            sim += r
        for r in ex.map(real_job, [(p, d, lat) for p in ("holdout_H1", "holdout_H2") for d in (10, 60, 180)]):
            real += r
    json.dump({"seed_base": SEED_BASE, "sim": sim, "real": real}, open(a.out, "w"), indent=1)
    print(len(sim), "sim rows,", len(real), "real rows written")


if __name__ == "__main__":
    main()
