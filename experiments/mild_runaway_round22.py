#!/usr/bin/env python3
"""Round 22: the wall. Mild runaways (slow, not hung) injected into real tool latencies; do class deadlines catch them?
Protocol: docs/TRACE_TEST_PLAN.md, round 22. Reuses round 21's loader, regimes and populations.

A runaway agent (half of each population, seeded) inflates 3% of its calls from slice 4 on: duration x uniform(2, 4), capped at 0.95 D, so a
static deadline never kills them. They are labeled bad. The system cannot see the label: completed bad calls enter the class's
duration history like any other (the poisoning the one-way rule is meant to resist). Honest agents are unchanged.
Regimes: recommended, recommended_budget_prov, recommended_slice_budget_prov (ms.sim.replay), class_oneway, class_twoway.
Deadlines 10, 60, 180 s; 10 seeds from 22000.

usage: python experiments/mild_runaway_round22.py --data PATH/syfi_coding_trace.jsonl.gz [--seeds 10]
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
import class_budget_round21 as c21   # noqa: E402

SEED_BASE, P_MILD, CAP_AT = 22000, 0.03, 0.95
SLICE, MARGIN, FLOOR, NMIN, Q = c21.SLICE, c21.MARGIN, c21.FLOOR, c21.NMIN, c21.Q


def class_regime(dur_ms, cls, bad, D, oneway):
    n = len(dur_ms)
    deadline, comp = {}, defaultdict(list)
    extra = legit = nbad = killed_bad = 0
    burn_g = burn_s = 0.0
    for s in range(0, n, SLICE):
        for c, obs in comp.items():
            if len(obs) >= NMIN:
                tgt = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q))))
                deadline[c] = min(deadline.get(c, D), tgt) if oneway else tgt
        for i in range(s, min(n, s + SLICE)):
            c = cls[i]; dl = deadline.get(c, D); x = dur_ms[i]
            if bad[i]:
                nbad += 1; burn_s += min(x, D); burn_g += min(x, dl)
                if x > dl:
                    killed_bad += 1
                else:
                    comp[c].append(x)                 # a completed bad call is indistinguishable from a good one
                continue
            legit += 1
            if x > dl:
                extra += 1 if x <= D else 0
            else:
                comp[c].append(x)
    return extra, legit, nbad, killed_bad, burn_g, burn_s


def job(args):
    pop, D_s, seed, data = args
    users, runaway, use, rng = c21.make_agents(data, pop, seed)
    D_ms = D_s * 1000.0
    trace, agents, cls_of, bad_of, dur_of, rows = {}, [], {}, {}, {}, []
    for u in users:
        lat, cl = data[u][0][:use].copy(), data[u][1][:use]
        n = len(lat); bad = np.zeros(n, bool)
        if u in runaway:
            m = (rng.random(n) < P_MILD) & (np.arange(n) >= c21.HANG_FROM_SLICE * SLICE)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), CAP_AT * D_ms); bad = m
        cls_of[u], bad_of[u], dur_of[u] = cl, bad, lat
        trace[u] = {"task_id": np.array([f"{u}-{i}" for i in range(n)], dtype=object), "seq": np.arange(n, dtype=np.int64),
                    "duration_ns": np.maximum(1, lat * 1e6).astype(np.int64), "tokens": np.full(n, 800, dtype=np.int64),
                    "memory": np.full(n, 120 * 2**20, dtype=np.int64), "expected": np.zeros(n, dtype=np.int64),
                    "is_bad": bad.astype(np.int64)}
        agents.append({"agent_id": u, "profile": "real", "should_tighten": int(u in runaway), "team": None})
    defaults = {"deadline_ns": int(D_s * 1e9), "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in c21.CAP_POLICIES}, slice_n=SLICE, tasks_per_agent=use)
    for pol, pr in res["policies"].items():
        for u, v in pr["agents"].items():
            s, g = v["static"], v["governed"]; d = dur_of[u]; ok = ~bad_of[u]
            B = 100.0 * float(((d[ok] > c21.F_CAP * D_ms) & (d[ok] <= D_ms)).mean())
            rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "regime": pol, "agent": u, "runaway": u in runaway,
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]), "bound_points": B,
                         "bad": g["bad"], "bad_killed": g["bad_caught"], "burn_static": s["bad_ns"], "burn_governed": g["bad_ns"]})
    for oneway, name in ((True, "class_oneway"), (False, "class_twoway")):
        for u in users:
            extra, legit, nbad, kb, bg, bs = class_regime(dur_of[u], cls_of[u], bad_of[u], D_ms, oneway)
            d = dur_of[u]; ok = ~bad_of[u]
            B = 100.0 * float(((d[ok] > FLOOR * D_ms) & (d[ok] <= D_ms)).mean())
            rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "regime": name, "agent": u, "runaway": u in runaway,
                         "cost_points": 100.0 * extra / max(1, legit), "bound_points": B, "bad": nbad, "bad_killed": kb,
                         "burn_static": bs, "burn_governed": bg})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "mild_runaway_round22.json"))
    ap.add_argument("--source", default="tracelab", choices=["tracelab", "freeinference"]); ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    a = ap.parse_args()
    if a.source == "freeinference":
        import freeinference_loader; data = freeinference_loader.load(a.data)
    else:
        data = c21.load(a.data)
    jobs = [(p, d, a.seed_base + i, data) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs):
            rows += r
    json.dump({"seed_base": a.seed_base, "rows": rows}, open(a.out, "w"))
    print(len(rows), "rows written")


if __name__ == "__main__":
    main()
