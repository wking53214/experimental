#!/usr/bin/env python3
"""Class split by program (docs/TRACE_TEST_PLAN.md, 'Round 23 class split by program'). Arms A0, A1, A2. usage: split_class_round23.py --fi DIR [--seeds 10]"""
import argparse, json, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import class_budget_round21 as c21   # noqa
import freeinference_loader          # noqa
NMIN, MARGIN, Q, FLOOR, SLICE = c21.NMIN, c21.MARGIN, c21.Q, c21.FLOOR, c21.SLICE

def sim(dur, parent, sub, bad, D, arm):
    n = len(dur); dl = {}; comp = defaultdict(list); extra = legit = 0
    burn_s = burn_g = 0.0; warm_unc = 0.0; classes = set()
    key = (lambda i: parent[i]) if arm == "A0" else (lambda i: sub[i])
    for s in range(0, n, SLICE):
        for k, obs in comp.items():
            if len(obs) >= NMIN:
                t = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q))))
                dl[k] = min(dl.get(k, D), t)
        for i in range(s, min(n, s + SLICE)):
            k = key(i); classes.add(k)
            d = dl.get(k)
            if d is None: d = dl.get(parent[i], D) if arm == "A2" else D
            x = dur[i]
            if bad[i]:
                burn_s += x; burn_g += min(x, d)
                if x <= d and len(comp[k]) >= NMIN: warm_unc += x
                continue
            legit += 1
            if x > d:
                if x <= D: extra += 1
            else:
                comp[k].append(x)
                if arm != "A0": comp[parent[i]].append(x)
    return extra, legit, burn_s, burn_g, warm_unc, len(classes)

def job(a):
    data, base, pop, Ds, seed = a
    users, runaway, use, rng = c21.make_agents(base, pop, seed); D = Ds * 1000.0; rows = []
    for u in users:
        lat, par, sub = data[u][0][:use].copy(), data[u][1][:use], data[u][2][:use]
        bad = np.zeros(len(lat), bool)
        if u in runaway:
            m = (rng.random(len(lat)) < 0.03) & (np.arange(len(lat)) >= 1000)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), 0.95 * D); bad = m
        ok = ~bad; bound = 100.0 * float(((lat[ok] > FLOOR * D) & (lat[ok] <= D)).mean())
        for arm in ("A0", "A1", "A2"):
            e, l, bs, bg, wu, nc = sim(lat, par, sub, bad, D, arm)
            rows.append(dict(pop=pop, D=Ds, seed=seed, agent=u, runaway=u in runaway, arm=arm, extra=e, legit=l, burn_s=bs, burn_g=bg,
                             warm_unc=wu, classes=nc, bound=bound))
    return rows

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--fi", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "split_class_round23.json")); a = ap.parse_args()
    data = freeinference_loader.load_split(a.fi); base = {u: (v[0], v[1]) for u, v in data.items()}
    jobs = [(data, base, p, d, 24000 + i) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs): rows += r
    json.dump(rows, open(a.out, "w")); print(len(rows), "rows")
