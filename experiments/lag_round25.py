#!/usr/bin/env python3
"""Round 25: honest agents that legitimately slow after tightening. Protocol: docs/TRACE_TEST_PLAN.md rounds 25 and 26."""
import argparse, json, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import class_budget_round21 as c21   # noqa: E402
SEED_BASE, SHIFT_SLICE, RESET_SLICE, FS = 25000, 8, 12, (1.5, 2.0, 3.0)
SLICE, MARGIN, FLOOR, NMIN, Q = c21.SLICE, c21.MARGIN, c21.FLOOR, c21.NMIN, c21.Q


def run(dur, cls, D, mode):
    n = len(dur); deadline, comp = {}, defaultdict(list); extra = legit = 0
    for s in range(0, n, SLICE):
        k = s // SLICE
        if mode == "reset" and k == RESET_SLICE:
            deadline.clear(); comp.clear()
        for c, obs in comp.items():
            if len(obs) >= NMIN:
                tgt = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q))))
                deadline[c] = tgt if mode == "twoway" else min(deadline.get(c, D), tgt)
        for i in range(s, min(n, s + SLICE)):
            c = cls[i]; dl = deadline.get(c, D); x = dur[i]
            post = k >= SHIFT_SLICE
            if post: legit += 1
            if x > dl:
                if post and x <= D: extra += 1
            else: comp[c].append(x)
    return extra, legit


def job(args):
    pop, D_s, seed, data = args
    users, shifters, use, rng = c21.make_agents(data, pop, seed)
    D = D_s * 1000.0; rows = []
    for u in users:
        t0, cl = data[u][0][:use].copy(), data[u][1][:use]
        for f in (1.0,) + FS:
            lat = t0.copy()
            if f > 1 and u in shifters:
                lat[SHIFT_SLICE * SLICE:] = np.minimum(lat[SHIFT_SLICE * SLICE:] * f, 0.95 * D)
            elif f > 1:
                continue
            post = lat[SHIFT_SLICE * SLICE:]
            B = 100.0 * float(((post > FLOOR * D) & (post <= D)).mean()) if len(post) else 0.0
            for mode in ("oneway", "twoway", "reset"):
                e, l = run(lat, cl, D, mode)
                rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "f": f, "mode": mode, "agent": u,
                             "shifter": u in shifters, "extra": e, "legit": l, "bound": B})
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "lag_round25.json")); a = ap.parse_args()
    data = c21.load(a.data)
    jobs = [(p, d, SEED_BASE + i, data) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs): rows += r
    json.dump(rows, open(a.out, "w")); print(len(rows), "rows written")
