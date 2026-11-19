#!/usr/bin/env python3
"""Prints round 19 tables and verdicts H1-H6 from experiments/red_team_round19.json."""
import json
from pathlib import Path
rows = json.load(open(Path(__file__).resolve().parent / "red_team_round19.json"))["rows"]
POL = ("recommended", "recommended_budget", "recommended_slice", "recommended_slice_budget")
def saved(xs):
    bs = sum(x["bad_ns_static"] for x in xs); bg = sum(x["bad_ns_governed"] for x in xs)
    return None if bs == 0 else 100 * (1 - bg / bs)
print("H1 identity-runs above 27.1%:", sum(1 for r in rows if r["bad_ns_static"] and 1 - r["bad_ns_governed"] / r["bad_ns_static"] > 0.271 + 1e-9),
      "| highest", round(max(100 * (1 - r["bad_ns_governed"] / r["bad_ns_static"]) for r in rows if r["bad_ns_static"]), 2))
print(f"\n{'group':<11}" + "".join(f"{p:>26}" for p in POL))
tab = {}
for g in ("persistent", "sybil6", "sybil3"):
    line = f"{g:<11}"
    for p in POL:
        v = saved([r for r in rows if r["policy"] == p and r["group"] == g]); tab[(g, p)] = v
        line += f"{v:>25.1f}%"
    print(line)
print("\nH6 persistent minus sybil6 under the budget:", round(tab[("persistent", "recommended_budget")] - tab[("sybil6", "recommended_budget")], 1), "points")
print("\nfirst tightened slice (median over runs, identities that were tightened), sybil6:")
import statistics
for p in POL:
    xs = [r["first_tightened_slice"] for r in rows if r["policy"] == p and r["group"] == "sybil6" and r["first_tightened_slice"] is not None]
    print(f"  {p:<26} tightened {len(xs)} of {sum(1 for r in rows if r['policy']==p and r['group']=='sybil6')} identity-runs; median slice {statistics.median(xs) if xs else None}")
