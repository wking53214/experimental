#!/usr/bin/env python3
"""Round 15: the two experiments a referee still demands (docs/TRACE_TEST_PLAN.md, round 15).

15a  run length x calm share: roster calm_v1 replayed for 32, 64 and 128 slices.
15b  net time against the misbehaving share: the program book at three deadlines, and three direct mixes.

Predictions were committed before any run. Raw per-agent aggregates go to experiments/stack_trace_round15{a,b}.json;
`python experiments/score_round15.py` prints the prediction tables from those files.

usage: python experiments/stack_trace_round15.py {a|b} [--seeds 20] [--seed-base 13000] [--workers N]
"""
import argparse, json, os, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import stack_trace_multiseed as ms   # noqa: E402

POLICIES = ("recommended", "recommended_slice", "twoway_h16")
LENGTH_SLICES = (32, 64, 128)
SLICE = 250


def job(args):
    roster, seed, tasks, pols = args
    ms.TASKS = tasks
    specs = {p: ("named", None) for p in pols}
    _r, _s, out = ms.one_job((roster, seed, specs))
    return roster, seed, tasks, out


def aggregate(runs):
    """-> {roster: {tasks: {policy: {agent: {...}}}}}"""
    acc = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    for roster, seed, tasks, out in runs:
        for pol, per in out.items():
            for agent, o in per.items():
                if agent.startswith("_"):
                    continue
                acc[roster][tasks][pol][agent].append(o)
    res = {}
    for roster, by_t in acc.items():
        res[roster] = {}
        for tasks, by_p in by_t.items():
            res[roster][str(tasks)] = {}
            for pol, agents in by_p.items():
                rows = {}
                for agent, outs in agents.items():
                    n = len(outs)
                    bs = sum(o["bad_ns_static"] for o in outs); bg = sum(o["bad_ns_governed"] for o in outs)
                    rows[agent] = {
                        "profile": outs[0]["profile"], "should_tighten": outs[0]["should_tighten"], "seeds": n,
                        "tightened_seeds": int(sum(o["tightened"] for o in outs)),
                        "saved_share": None if bs == 0 else 1.0 - bg / bs,
                        "legit_cost_points_mean": float(np.mean([o["legit_cost_points"] for o in outs])),
                        "net_burn_ns_mean": float(np.mean([o["net_burn_ns"] for o in outs])),
                        "net_rerun_ns_mean": float(np.mean([o["net_rerun_ns"] for o in outs])),
                        "net_burn_negative_seeds": int(sum(o["net_burn_ns"] < 0 for o in outs)),
                        "total_ns_static_mean": float(np.mean([o["total_ns_static"] for o in outs])),
                        "bad_ns_static_mean": float(np.mean([o["bad_ns_static"] for o in outs])),
                        "operator_decisions_total": int(sum(o.get("operator_decisions", 0) for o in outs)),
                        "net_burn_ns_per_seed": [o["net_burn_ns"] for o in outs],
                        "total_ns_static_per_seed": [o["total_ns_static"] for o in outs],
                    }
                res[roster][str(tasks)][pol] = rows
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=("a", "b"))
    ap.add_argument("--seeds", type=int, default=20); ap.add_argument("--seed-base", type=int, default=13000)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    seeds = [a.seed_base + i for i in range(1, a.seeds + 1)]
    if a.part == "a":
        jobs = [("calm_v1", s, n * SLICE, POLICIES) for n in LENGTH_SLICES for s in seeds]
    else:
        rosters = ("book_v1", "book75_v1", "book60_v1", "mix25_v1", "mix50_v1", "mix75_v1")
        jobs = [(r, s, 8000, POLICIES) for r in rosters for s in seeds]
    with ProcessPoolExecutor(a.workers) as ex:
        runs = list(ex.map(job, jobs))
    out = {"part": a.part, "seeds": seeds, "policies": list(POLICIES), "results": aggregate(runs)}
    path = a.out or str(ROOT / "experiments" / f"stack_trace_round15{a.part}.json")
    Path(path).write_text(json.dumps(out, indent=1))
    print("wrote", path)


if __name__ == "__main__":
    main()
