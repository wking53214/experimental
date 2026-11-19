#!/usr/bin/env python3
"""Prints the round 16 tables and prediction verdicts from experiments/real_trace_round16.json."""
import json
from pathlib import Path
import numpy as np
rows = json.load(open(Path(__file__).resolve().parent / "real_trace_round16.json"))["rows"]
POL = ("recommended", "recommended_slice", "twoway_h16")
def sel(**k): return [r for r in rows if all(r[a] == b for a, b in k.items())]
def rank(x):
    o = np.argsort(x, kind="stable"); r = np.empty(len(x)); r[o] = np.arange(len(x)); return r
print("R1 violations:", sum(1 for r in rows if r["cost_points"] > r["bound_points"] + 1e-9), "of", len(rows),
      "| closest slack", round(min(r["bound_points"] - r["cost_points"] for r in rows), 4),
      "| min final deadline ratio", round(min(r["final_deadline_ratio"] for r in rows), 4))
for r in sorted(rows, key=lambda r: r["cost_points"] - r["bound_points"], reverse=True)[:3]:
    print("   closest to violating:", r["pop"], r["deadline_s"], r["policy"], round(r["cost_points"], 3), "vs bound", round(r["bound_points"], 3))
print("\nR2/R3 (all agents, both populations pooled) by deadline, policy: share tightened | mean cost | mean bound | mean static fail %")
for d in (10, 60, 180):
    for p in POL:
        xs = sel(deadline_s=d, policy=p)
        print(f"  {d:>3}s {p:<18} {100*np.mean([x['tightened'] for x in xs]):5.1f}% | {np.mean([x['cost_points'] for x in xs]):6.2f} | "
              f"{np.mean([x['bound_points'] for x in xs]):6.2f} | {np.mean([x['static_fail_pct'] for x in xs]):6.2f}   (n={len(xs)})")
print("\nR2/R3 by population, policy recommended:")
for pop in ("long", "wide"):
    for d in (10, 60, 180):
        xs = sel(pop=pop, deadline_s=d, policy="recommended")
        print(f"  {pop:<4} {d:>3}s tightened {100*np.mean([x['tightened'] for x in xs]):5.1f}% mean cost {np.mean([x['cost_points'] for x in xs]):5.2f} max {max(x['cost_points'] for x in xs):5.2f} (n={len(xs)})")
big = [(r, next(q for q in rows if q["pop"] == r["pop"] and q["deadline_s"] == r["deadline_s"] and q["agent"] == r["agent"] and q["policy"] == "twoway_h16"))
       for r in rows if r["policy"] == "recommended_slice" and r["cost_points"] > 0.5]
print("\nR4: cells where recommended_slice > 0.5 points:", len(big), "| twoway_h16 cheaper in",
      sum(1 for a, b in big if b["cost_points"] < a["cost_points"]), "=", round(100 * sum(1 for a, b in big if b["cost_points"] < a["cost_points"]) / max(1, len(big)), 1), "%")
xs = [r for r in rows if r["bound_points"] > 0.5]
if len(xs) > 2:
    rho = np.corrcoef(rank([x["bound_points"] for x in xs]), rank([x["cost_points"] for x in xs]))[0, 1]
    print("R5: cells with bound > 0.5:", len(xs), "| Spearman", round(float(rho), 3))
else: print("R5: too few cells with bound > 0.5:", len(xs))
