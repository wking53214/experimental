#!/usr/bin/env python3
"""Prints round 18 tables and verdicts G1-G6 from experiments/red_team_round18.json."""
import json
from pathlib import Path
import numpy as np
rows = json.load(open(Path(__file__).resolve().parent / "red_team_round18.json"))["rows"]
POL = ("recommended", "recommended_budget", "recommended_slice", "recommended_slice_budget")
def sel(**k): return [r for r in rows if all(r[a] == b for a, b in k.items())]
def saved(xs):
    bs = sum(x["bad_ns_static"] for x in xs); bg = sum(x["bad_ns_governed"] for x in xs)
    return None if bs == 0 else 1 - bg / bs
hv = [r for r in rows if not r["should_tighten"] and r["cost_points"] > r["bound_points"] + 1e-9]
sv = [(r["agent"], r["policy"], r["seed"], saved([r])) for r in rows if r["should_tighten"] and (saved([r]) or 0) > 0.271 + 1e-9]
print("G1 honest violations:", len(hv), "| attacker saved > 27.1%:", len(sv), "| max single-run saved share", round(100 * max((saved([r]) or 0) for r in rows if r["should_tighten"]), 3))
print()
for a in ("bandstack-honest", "stepchange-honest"):
    for p in POL:
        xs = sel(agent=a, policy=p)
        c = [x["cost_points"] for x in xs]
        print(f"{a:<18} {p:<24} cost mean {np.mean(c):6.2f} max {max(c):6.2f} | bound mean {np.mean([x['bound_points'] for x in xs]):6.2f} | seeds >1.0 pt: {sum(1 for v in c if v > 1.0)}/{len(c)} | final ratio {np.mean([x['final_deadline_ratio'] for x in xs]):.3f}")
print()
for p in POL:
    xs = sel(agent="floorpump-attacker", policy=p)
    print(f"floorpump          {p:<24} pooled saved {100*(saved(xs) or 0):5.1f}% | worst seed {100*min((saved([x]) or 0) for x in xs):5.1f}% | final ratio {np.mean([x['final_deadline_ratio'] for x in xs]):.3f}")
print()
for p in POL:
    xs = [r for r in rows if r["policy"] == p and r["agent"].startswith("sybil")]
    per = {k: 100 * (saved([r for r in xs if r["agent"] == f"sybil-{k}"]) or 0) for k in range(6)}
    print(f"sybil (pooled)     {p:<24} saved {100*(saved(xs) or 0):5.1f}% | per identity {({k: round(v,1) for k,v in per.items()})}")
neg = [(r['agent'], r['policy'], r['seed']) for r in rows if r["should_tighten"] and r['policy'].endswith('budget') and (saved([r]) or 0) < -1e-9]
print("\nG6 attacker runs worse than static under a budget policy:", len(neg))
