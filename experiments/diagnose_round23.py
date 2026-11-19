#!/usr/bin/env python3
"""Where does the class-deadline saving go, and which settings could raise it? Exploratory (NOT pre-registered): settings are chosen on TraceLab
and then checked once on FreeInference, which was not used for choosing. usage: diagnose_round23.py --tracelab F.jsonl.gz --fi DIR"""
import argparse, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import class_budget_round21 as c21   # noqa
import freeinference_loader          # noqa
BASE = dict(margin=1.5, q=0.99, nmin=30, floor=0.25, slice=250)

def sim(dur, cls, bad, D, P, oneway=True):
    n = len(dur); dl = {}; comp = defaultdict(list); extra = legit = 0
    cat = dict(cold=0.0, uncaught=0.0, caught=0.0, total=0.0)   # bad burn (ms) by outcome, static basis
    for s in range(0, n, P["slice"]):
        for c, obs in comp.items():
            if len(obs) >= P["nmin"]:
                t = min(D, max(P["floor"] * D, P["margin"] * float(np.quantile(obs, P["q"]))))
                dl[c] = min(dl.get(c, D), t) if oneway else t
        for i in range(s, min(n, s + P["slice"])):
            c = cls[i]; d = dl.get(c, D); x = dur[i]
            if bad[i]:
                cat["total"] += x
                if x > d: cat["caught"] += x - d
                elif len(comp[c]) < P["nmin"]: cat["cold"] += x - d if d < D else x
                else: cat["uncaught"] += x
                continue
            legit += 1
            if x > d:
                if x <= D: extra += 1
            else: comp[c].append(x)
    return extra, legit, cat

def job(a):
    data, pop, Ds, seed, P = a
    users, runaway, use, rng = c21.make_agents(data, pop, seed); D = Ds * 1000.0
    ex = lg = 0; tot = dict(cold=0.0, uncaught=0.0, caught=0.0, total=0.0); ceil = 0.0
    for u in users:
        lat, cl = data[u][0][:use].copy(), data[u][1][:use]; bad = np.zeros(len(lat), bool)
        if u in runaway:
            m = (rng.random(len(lat)) < 0.03) & (np.arange(len(lat)) >= 1000)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), 0.95 * D); bad = m
        e, l, cat = sim(lat, cl, bad, D, P)
        if u in runaway:
            for k in tot: tot[k] += cat[k]
            ceil += float(np.maximum(0, lat[bad] - P["floor"] * D).sum())
        else: ex += e; lg += l
    return tot, ceil, ex, lg

def run(data, P, Ds=60, seeds=range(3)):
    jobs = [(data, p, Ds, 23100 + s, P) for p in ("wide", "long") for s in seeds]
    with ProcessPoolExecutor() as pool: res = list(pool.map(job, jobs))
    T = sum(r[0]["total"] for r in res); sav = sum(r[0]["caught"] for r in res); cold = sum(r[0]["cold"] for r in res)
    unc = sum(r[0]["uncaught"] for r in res); ce = sum(r[1] for r in res); ex = sum(r[2] for r in res); lg = sum(r[3] for r in res)
    return dict(saved=100 * sav / T, cold=100 * cold / T, uncaught=100 * unc / T, ceiling=100 * ce / T, cost=100 * ex / lg)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--tracelab"); ap.add_argument("--fi"); a = ap.parse_args()
    sets = {"tracelab": c21.load(a.tracelab), "fi": freeinference_loader.load(a.fi)}
    print("== breakdown of runaway burn at 60 s with the shipped settings (share of the burn, %)")
    for k, d in sets.items():
        r = run(d, BASE); print(f"{k:9} saved {r['saved']:5.1f}  never-warm {r['cold']:5.1f}  warm-but-uncaught {r['unc'] if 'unc' in r else r['uncaught']:5.1f}  floor ceiling {r['ceiling']:5.1f}  honest cost {r['cost']:.2f}")
    grid = [("shipped", {}), ("margin 1.2", dict(margin=1.2)), ("margin 1.0", dict(margin=1.0)), ("q 0.95", dict(q=0.95)), ("q 0.90", dict(q=0.90)),
            ("nmin 15", dict(nmin=15)), ("nmin 10", dict(nmin=10)), ("floor 0.15", dict(floor=0.15)), ("slice 50", {"slice": 50}),
            ("margin 1.2 + nmin 15", dict(margin=1.2, nmin=15)), ("margin 1.2 + q .95 + nmin 15", dict(margin=1.2, q=0.95, nmin=15))]
    print("\n== settings sweep at 60 s (saved %, honest cost points); chosen on tracelab, fi shown for confirmation")
    for name, ch in grid:
        P = dict(BASE, **ch); t = run(sets["tracelab"], P); f = run(sets["fi"], P)
        print(f"{name:30} tracelab {t['saved']:5.1f} / {t['cost']:.2f}   fi {f['saved']:5.1f} / {f['cost']:.2f}")
