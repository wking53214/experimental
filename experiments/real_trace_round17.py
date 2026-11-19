#!/usr/bin/env python3
"""Round 17: the cost budget (TrapPolicy.cost_budget, docs/CERTIFICATE.md) tested on held-out stretches of real
coding-agent latencies and on fresh simulation seeds. Protocol fixed in docs/TRACE_TEST_PLAN.md, round 17.

  real, in-sample : the 32 round-16 agents, same windows (diagnosis data; the budget was designed after seeing it)
  real, hold-out  : H1 users with >= 4000 calls, calls [2000, 4000); H2 users with >= 16000 calls, calls [8000, 16000)
                    (time stretches round 16 never read; same people, so not independent users)
  simulated       : rosters v1 and book_v1, seeds 16000..16019, 8000 tasks per agent
Policies: recommended, recommended_budget, recommended_slice, recommended_slice_budget (budget 1%).

usage: python experiments/real_trace_round17.py --data PATH/syfi_coding_trace.jsonl.gz [--seeds 20] [--out-real ...] [--out-sim ...]
"""
import argparse, json, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import stack_trace_multiseed as ms   # noqa: E402
import gen_trace                     # noqa: E402
import real_trace_round16 as r16     # noqa: E402

POLICIES = ("recommended", "recommended_budget", "recommended_slice", "recommended_slice_budget")
DEADLINES_S = (10, 60, 180)
SLICE = 250
# name: (min calls, start, use)
REAL_POPS = {"insample_long": (8000, 0, 8000), "insample_wide": (2000, 0, 2000),
             "holdout_H1": (4000, 2000, 2000), "holdout_H2": (16000, 8000, 8000)}
SIM_ROSTERS = ("v1", "book_v1")
SIM_SEED_BASE = 16000


def build(lat_by_user, min_calls, start, use):
    users = sorted(u for u, a in lat_by_user.items() if len(a) >= min_calls)
    trace, agents = {}, []
    for u in users:
        a = lat_by_user[u][start:start + use]
        n = len(a)
        trace[u] = {"task_id": np.array([f"{u}-{start + i}" for i in range(n)], dtype=object), "seq": np.arange(n, dtype=np.int64),
                    "duration_ns": np.maximum(1, (a * 1e6)).astype(np.int64),
                    "tokens": np.full(n, 800, dtype=np.int64), "memory": np.full(n, 120 * 2**20, dtype=np.int64),
                    "expected": np.zeros(n, dtype=np.int64), "is_bad": np.zeros(n, dtype=np.int64)}
        agents.append({"agent_id": u, "profile": "real", "should_tighten": 0, "team": None})
    return trace, agents


def real_job(args):
    pop, dl_s, lat = args
    mn, start, use = REAL_POPS[pop]
    trace, agents = build(lat, mn, start, use)
    D = int(dl_s * 1e9)
    defaults = {"deadline_ns": D, "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in POLICIES}, slice_n=SLICE, tasks_per_agent=use)
    rows = []
    for pol, pr in res["policies"].items():
        for u, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            d = trace[u]["duration_ns"]
            rows.append({"pop": pop, "deadline_s": dl_s, "policy": pol, "agent": u, "calls": int(len(d)),
                         "static_fail_pct": 100.0 * s["legit_failed"] / max(1, s["legit"]),
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]),
                         "bound_points": 100.0 * float(((d > r16.F * D) & (d <= D)).mean()),
                         "tightened": bool(v["tightened"]), "first_slice": v["first_tightened_slice"],
                         "final_deadline_ratio": (v["limits"]["deadline_ns"]["limit"] if v["limits"]["deadline_ns"] else D) / D})
    return rows


def sim_job(args):
    roster, seed = args
    ms.TASKS = 8000
    specs = {p: ("named", None) for p in POLICIES}
    _r, _s, out = ms.one_job((roster, seed, specs))
    return roster, seed, out


def aggregate_sim(runs):
    acc = defaultdict(list)
    for roster, seed, out in runs:
        for pol, per in out.items():
            for agent, o in per.items():
                if not agent.startswith("_"):
                    acc[(roster, pol, agent)].append(o)
    res = {}
    for (roster, pol, agent), outs in acc.items():
        bs = sum(o["bad_ns_static"] for o in outs); bg = sum(o["bad_ns_governed"] for o in outs)
        res.setdefault(roster, {}).setdefault(pol, {})[agent] = {
            "profile": outs[0]["profile"], "should_tighten": outs[0]["should_tighten"], "seeds": len(outs),
            "tightened_seeds": int(sum(o["tightened"] for o in outs)),
            "saved_share": None if bs == 0 else 1.0 - bg / bs,
            "legit_cost_points_mean": float(np.mean([o["legit_cost_points"] for o in outs])),
            "legit_cost_points_max": float(np.max([o["legit_cost_points"] for o in outs])),
            "net_burn_ns_mean": float(np.mean([o["net_burn_ns"] for o in outs])),
            "operator_decisions_total": int(sum(o["operator_decisions"] for o in outs))}
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out-real", default=str(ROOT / "experiments" / "real_trace_round17_real.json"))
    ap.add_argument("--out-sim", default=str(ROOT / "experiments" / "real_trace_round17_sim.json"))
    a = ap.parse_args()
    lat = r16.load(a.data)
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(real_job, [(p, d, lat) for p in REAL_POPS for d in DEADLINES_S]):
            rows += r
        json.dump({"source": "uw-syfi/TraceLab v0.0.1 (CC BY 4.0)", "rows": rows}, open(a.out_real, "w"), indent=1)
        runs = list(ex.map(sim_job, [(r, SIM_SEED_BASE + i) for r in SIM_ROSTERS for i in range(a.seeds)]))
    json.dump({"seeds": a.seeds, "seed_base": SIM_SEED_BASE, "policies": POLICIES, "results": aggregate_sim(runs)},
              open(a.out_sim, "w"), indent=1)
    print(len(rows), "real cells,", len(runs), "simulated runs written")


if __name__ == "__main__":
    main()
