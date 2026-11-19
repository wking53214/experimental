#!/usr/bin/env python3
"""Prints round 20 tables and verdicts I1-I5 from experiments/red_team_round20.json."""
import json
from pathlib import Path
import numpy as np
d = json.load(open(Path(__file__).resolve().parent / "red_team_round20.json"))
sim, real = d["sim"], d["real"]
POL = ("recommended", "recommended_budget", "recommended_budget_prov", "recommended_slice", "recommended_slice_budget", "recommended_slice_budget_prov")
def saved(xs):
    bs = sum(x["bad_ns_static"] for x in xs); bg = sum(x["bad_ns_governed"] for x in xs)
    return None if bs == 0 else 100 * (1 - bg / bs)
att = [r for r in sim if r["should_tighten"] and r["bad_ns_static"]]
print("I1 attacker identity-runs above 27.1%:", sum(1 for r in att if 1 - r["bad_ns_governed"] / r["bad_ns_static"] > 0.271 + 1e-9),
      "| highest", round(100 * max(1 - r["bad_ns_governed"] / r["bad_ns_static"] for r in att), 2),
      "| honest violations sim:", sum(1 for r in sim if not r["should_tighten"] and r["cost_points"] > r["bound_points"] + 1e-9),
      "| real:", sum(1 for r in real if r["cost_points"] > r["bound_points"] + 1e-9), "of", len(real))
print(f"\nsaved share of hung time\n{'group':<11}" + "".join(f"{p.replace('recommended','rec'):>20}" for p in POL))
for g in ("persistent", "sybil6", "sybil3"):
    print(f"{g:<11}" + "".join(f"{saved([r for r in sim if r['policy']==p and r['group']==g]):>19.1f}%" for p in POL))
print(f"\nhonest newcomers, mean cost points (bound mean {np.mean([r['bound_points'] for r in sim if r['group']=='newcomer' and r['policy']=='recommended']):.2f})")
for p in POL:
    xs = [r["cost_points"] for r in sim if r["group"] == "newcomer" and r["policy"] == p]
    print(f"  {p:<30} {np.mean(xs):6.2f}  max {max(xs):6.2f}")
print("\nreal hold-out, mean cost points by deadline")
for dl in (10, 60, 180):
    print(f"  {dl:>3}s " + "  ".join(f"{p.replace('recommended','rec')} {np.mean([r['cost_points'] for r in real if r['deadline_s']==dl and r['policy']==p]):.3f}" for p in ("recommended", "recommended_budget", "recommended_budget_prov")))
pool = {p: np.mean([r["cost_points"] for r in real if r["policy"] == p]) for p in ("recommended", "recommended_budget", "recommended_budget_prov")}
print("  pooled:", {k: round(v, 3) for k, v in pool.items()}, "| prov minus budget:", round(pool["recommended_budget_prov"] - pool["recommended_budget"], 3))
