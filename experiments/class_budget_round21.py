#!/usr/bin/env python3
"""Round 21: class-conditioned budgeted deadlines (prototype) on real coding-agent latencies with injected, labeled hangs.
Protocol: docs/TRACE_TEST_PLAN.md, round 21.

Real calls (TraceLab v0.0.1, CC BY 4.0) keep their measured latency and tool name. Half of each population's agents (by a seeded
draw) are "runaway agents": from slice 4 on, each call has a 2% chance of being replaced by a hang of uniform(3, 9) x D (labeled bad).
The other half are honest (no change). A call never changes class. Deadlines D = 10, 60, 180 s; populations: wide (users with at
least 2000 calls, first 2000) and long (at least 8000, first 8000); 10 seeds from 21000.

Regimes
  cap policies (existing code, ms.sim.replay): recommended, recommended_budget_prov, recommended_slice_budget_prov
  class_oneway : per (agent, tool) deadline = min(previous, clamp(1.5 x q99 of that class's completed durations, 0.25 D, D)),
                 re-read at each slice start once the class has 30 completions; never loosens
  class_twoway : the same target without the min (it may rise back toward D); the comparison for the price of the one-way rule

usage: python experiments/class_budget_round21.py --data PATH/syfi_coding_trace.jsonl.gz [--seeds 10]
"""
import argparse, gzip, json, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import stack_trace_multiseed as ms   # noqa: E402
import gen_trace                     # noqa: E402

CAP_POLICIES = ("recommended", "recommended_budget_prov", "recommended_slice_budget_prov")
DEADLINES_S = (10, 60, 180)
POPS = {"wide": (2000, 2000), "long": (8000, 8000)}
SLICE, MARGIN, FLOOR, NMIN, Q = 250, 1.5, 0.25, 30, 0.99
P_HANG, HANG_FROM_SLICE, SEED_BASE = 0.02, 4, 21000
F_CAP = 0.9 ** 3


def load(path):
    per = defaultdict(list)
    for line in gzip.open(path, "rt"):
        r = json.loads(line)
        for t in r["tools"]:
            lat = t.get("tool_wall_latency_ms")
            if lat is None:
                lat = t.get("tool_internal_latency_ms")
            if lat is None:
                continue
            per[r["user"]].append((t.get("emitted_at") or "", float(lat), t.get("tool_name") or "none"))
    out = {}
    for u, v in per.items():
        v.sort(key=lambda x: x[0])
        out[u] = (np.array([x[1] for x in v]), np.array([x[2] for x in v], dtype=object))
    return out


def make_agents(data, pop, seed):
    mn, use = POPS[pop]
    rng = np.random.default_rng(np.random.SeedSequence([seed, {"wide": 1, "long": 2}[pop]]))
    users = sorted(u for u, (a, _) in data.items() if len(a) >= mn)
    runaway = set(u for u in users if rng.random() < 0.5)
    return users, runaway, use, rng


def class_regime(dur_ms, cls, bad, D, oneway):
    """Slice by slice; returns (honest extra kills, legit calls, burned bad time governed, burned bad time static)."""
    n = len(dur_ms)
    deadline = {}                       # class -> current deadline (ms)
    comp = defaultdict(list)            # class -> completed durations (ms)
    extra = bad_g = bad_s = 0.0
    legit = 0
    for s in range(0, n, SLICE):
        if True:
            for c, obs in comp.items():
                if len(obs) >= NMIN:
                    tgt = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q))))
                    deadline[c] = min(deadline.get(c, D), tgt) if oneway else tgt
        for i in range(s, min(n, s + SLICE)):
            c = cls[i]; dl = deadline.get(c, D); x = dur_ms[i]
            if bad[i]:
                bad_g += dl; bad_s += D
                continue
            legit += 1
            if x > dl:
                if x <= D:
                    extra += 1
            else:
                comp[c].append(x)
    return extra, legit, bad_g, bad_s


def job(args):
    pop, D_s, seed, data = args
    users, runaway, use, rng = make_agents(data, pop, seed)
    D_ms = D_s * 1000.0
    rows, trace, agents = [], {}, []
    cls_of, bad_of, dur_of = {}, {}, {}
    for u in users:
        lat, cl = data[u][0][:use].copy(), data[u][1][:use]
        n = len(lat); bad = np.zeros(n, bool)
        if u in runaway:
            hang = (rng.random(n) < P_HANG) & (np.arange(n) >= HANG_FROM_SLICE * SLICE)
            lat[hang] = rng.uniform(3, 9, hang.sum()) * D_ms; bad = hang
        cls_of[u], bad_of[u], dur_of[u] = cl, bad, lat
        trace[u] = {"task_id": np.array([f"{u}-{i}" for i in range(n)], dtype=object), "seq": np.arange(n, dtype=np.int64),
                    "duration_ns": np.maximum(1, lat * 1e6).astype(np.int64), "tokens": np.full(n, 800, dtype=np.int64),
                    "memory": np.full(n, 120 * 2**20, dtype=np.int64), "expected": np.zeros(n, dtype=np.int64),
                    "is_bad": bad.astype(np.int64)}
        agents.append({"agent_id": u, "profile": "real", "should_tighten": int(u in runaway), "team": None})
    defaults = {"deadline_ns": int(D_s * 1e9), "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in CAP_POLICIES}, slice_n=SLICE, tasks_per_agent=use)
    for pol, pr in res["policies"].items():
        for u, v in pr["agents"].items():
            s, g = v["static"], v["governed"]; d = dur_of[u]; ok = ~bad_of[u]
            B = 100.0 * float(((d[ok] > F_CAP * D_ms) & (d[ok] <= D_ms)).mean())
            rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "regime": pol, "agent": u, "runaway": u in runaway,
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]), "bound_points": B,
                         "bad_static": s["bad_ns"], "bad_governed": g["bad_ns"]})
    for oneway, name in ((True, "class_oneway"), (False, "class_twoway")):
        for u in users:
            extra, legit, bg, bs = class_regime(dur_of[u], cls_of[u], bad_of[u], D_ms, oneway)
            d = dur_of[u]; ok = ~bad_of[u]
            B = 100.0 * float(((d[ok] > FLOOR * D_ms) & (d[ok] <= D_ms)).mean())
            rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "regime": name, "agent": u, "runaway": u in runaway,
                         "cost_points": 100.0 * extra / max(1, legit), "bound_points": B, "bad_static": bs, "bad_governed": bg})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "class_budget_round21.json"))
    a = ap.parse_args()
    data = load(a.data)
    jobs = [(p, d, SEED_BASE + i, data) for p in POPS for d in DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs):
            rows += r
    json.dump({"seed_base": SEED_BASE, "rows": rows}, open(a.out, "w"))
    print(len(rows), "rows written")


if __name__ == "__main__":
    main()
