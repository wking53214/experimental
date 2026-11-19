#!/usr/bin/env python3
"""Round 26: simulated progress heartbeat as a tighten-only stall kill. Protocol: docs/TRACE_TEST_PLAN.md rounds 25 and 26."""
import argparse, json, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import class_budget_round21 as c21   # noqa: E402
import mild_runaway_round22 as c22   # noqa: E402
SEED_BASE, W = 26000, 0.1
SLICE, MARGIN, FLOOR, NMIN, Q = c21.SLICE, c21.MARGIN, c21.FLOOR, c21.NMIN, c21.Q


def run(dur, cls, bad, stall_at, D, hb):
    """stall_at[i] = time at which call i stops making progress (inf if it never stalls). hb: heartbeat kill on or off."""
    n = len(dur); deadline, comp = {}, defaultdict(list); extra = legit = nbad = 0; bg = bs = 0.0
    for s in range(0, n, SLICE):
        for c, obs in comp.items():
            if len(obs) >= NMIN:
                tgt = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q))))
                deadline[c] = min(deadline.get(c, D), tgt)
        for i in range(s, min(n, s + SLICE)):
            c = cls[i]; dl = deadline.get(c, D); x = dur[i]
            if hb and np.isfinite(stall_at[i]):
                dl = min(dl, max(FLOOR * D, stall_at[i] + W * D))
            if bad[i]:
                nbad += 1; bs += min(x, D); bg += min(x, dl)
                if x <= dl: comp[c].append(x)
                continue
            legit += 1
            if x > dl:
                if x <= D: extra += 1
            else: comp[c].append(x)
    return extra, legit, bg, bs


def job(args):
    pop, D_s, seed, data = args
    users, runaway, use, rng = c21.make_agents(data, pop, seed)
    D = D_s * 1000.0; rows = []
    for u in users:
        lat, cl = data[u][0][:use].copy(), data[u][1][:use]
        n = len(lat); bad = np.zeros(n, bool)
        if u in runaway:
            m = (rng.random(n) < c22.P_MILD) & (np.arange(n) >= c21.HANG_FROM_SLICE * SLICE)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), c22.CAP_AT * D); bad = m
        ok = ~bad
        B = 100.0 * float(((lat[ok] > FLOOR * D) & (lat[ok] <= D)).mean())
        u1, u2, u3 = rng.random(n), rng.random(n), rng.random(n)
        e, l, bg, bs = run(lat, cl, bad, np.full(n, np.inf), D, False)
        rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "r": 0.0, "q": 0.0, "mode": "base", "agent": u,
                     "runaway": u in runaway, "extra": e, "legit": l, "bg": bg, "bs": bs, "bound": B})
        for r in (0.9, 0.6):
            for q in (0.02, 0.1, 0.3):
                stall = np.full(n, np.inf)
                det = bad & (u1 < r)
                fs = ok & (lat > FLOOR * D) & (u2 < q)
                stall[det | fs] = (0.2 + 0.4 * u3[det | fs]) * lat[det | fs]
                e, l, bg, bs = run(lat, cl, bad, stall, D, True)
                rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "r": r, "q": q, "mode": "hb", "agent": u,
                             "runaway": u in runaway, "extra": e, "legit": l, "bg": bg, "bs": bs, "bound": B})
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "heartbeat_round26.json")); a = ap.parse_args()
    data = c21.load(a.data)
    jobs = [(p, d, SEED_BASE + i, data) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs): rows += r
    json.dump(rows, open(a.out, "w")); print(len(rows), "rows written")
