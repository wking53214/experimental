#!/usr/bin/env python3
"""Round 16: replay REAL coding-agent tool-call latencies (TraceLab, uw-syfi, CC BY 4.0) through the simulator.

Protocol (fixed before any run, docs/TRACE_TEST_PLAN.md, round 16):
  * source: syfi_coding_trace.jsonl.gz, release v0.0.1 (sha256 9d265eae...da0b4e6b); one agent per pseudonymous user.
  * a task is one tool call; duration is tool_wall_latency_ms (else tool_internal_latency_ms); calls with neither are dropped;
    calls are ordered by emitted_at within a user.
  * every real call is treated as legitimate (is_bad=0): the data carries no runaway label.
  * tokens and memory are held at the nominal values the generator uses, so only the deadline limit can bind.
  * populations: "long" = users with at least 8000 calls, first 8000 each (32 slices); "wide" = users with at least 2000
    calls, first 2000 each (8 slices).
  * deadlines: 10 s, 60 s, 180 s. Policies: recommended, recommended_slice, twoway_h16.
  * certificate: B = share of an agent's calls with latency in (0.9**3 * D, D]; honest cost (points) must not exceed 100*B.

usage: python experiments/real_trace_round16.py --data PATH/syfi_coding_trace.jsonl.gz [--out experiments/real_trace_round16.json]
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

POLICIES = ("recommended", "recommended_slice", "twoway_h16")
DEADLINES_S = (10, 60, 180)
POPS = {"long": (8000, 8000), "wide": (2000, 2000)}   # name: (min calls, calls used)
F = 0.9 ** 3
SLICE = 250


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
            per[r["user"]].append((t.get("emitted_at") or "", float(lat)))
    return {u: np.array([l for _, l in sorted(v, key=lambda x: x[0])]) for u, v in per.items()}


def build(lat_by_user, min_calls, use):
    users = sorted(u for u, a in lat_by_user.items() if len(a) >= min_calls)
    trace, agents = {}, []
    for u in users:
        a = lat_by_user[u][:use]
        n = len(a)
        trace[u] = {"task_id": np.array([f"{u}-{i}" for i in range(n)], dtype=object), "seq": np.arange(n, dtype=np.int64),
                    "duration_ns": np.maximum(1, (a * 1e6)).astype(np.int64),
                    "tokens": np.full(n, 800, dtype=np.int64), "memory": np.full(n, 120 * 2**20, dtype=np.int64),
                    "expected": np.zeros(n, dtype=np.int64), "is_bad": np.zeros(n, dtype=np.int64)}
        agents.append({"agent_id": u, "profile": "real", "should_tighten": 0, "team": None})
    return trace, agents


def job(args):
    pop, dl_s, lat_by_user = args
    mn, use = POPS[pop]
    trace, agents = build(lat_by_user, mn, use)
    D = int(dl_s * 1e9)
    defaults = {"deadline_ns": D, "tokens_capacity": gen_trace.TOKENS_CAP, "memory_capacity_bytes": gen_trace.MEM_CAP}
    res = ms.sim.replay(trace, agents, defaults, {p: ms.sim.POLICIES[p] for p in POLICIES}, slice_n=SLICE, tasks_per_agent=use)
    rows = []
    for pol, pr in res["policies"].items():
        for u, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            d = trace[u]["duration_ns"]
            B = 100.0 * float(((d > F * D) & (d <= D)).mean())
            rows.append({"pop": pop, "deadline_s": dl_s, "policy": pol, "agent": u, "calls": int(len(d)),
                         "static_fail_pct": 100.0 * s["legit_failed"] / max(1, s["legit"]),
                         "cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]),
                         "bound_points": B, "tightened": bool(v["tightened"]), "first_slice": v["first_tightened_slice"],
                         "final_deadline_ratio": (v["limits"]["deadline_ns"]["limit"] if v["limits"]["deadline_ns"] else D) / D,
                         "operator_decisions": v.get("operator_decisions", 0)})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "real_trace_round16.json"))
    a = ap.parse_args()
    lat = load(a.data)
    jobs = [(p, d, lat) for p in POPS for d in DEADLINES_S]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs):
            rows += r
    json.dump({"source": "uw-syfi/TraceLab v0.0.1 (CC BY 4.0)", "rows": rows}, open(a.out, "w"), indent=1)
    print(len(rows), "cells written to", a.out)


if __name__ == "__main__":
    main()
