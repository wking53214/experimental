#!/usr/bin/env python3
"""Three-arm comparison (docs/TRACE_TEST_PLAN.md, "Round 23 comparative test"). Writes raw per-run rows to compare_round23_arms.csv."""
import csv, json
from pathlib import Path
import numpy as np
H = Path(__file__).parent
R22 = json.load(open(H / "mild_runaway_round22.json"))["rows"]
R23 = json.load(open(H / "class_kernel_replay_round23.json"))
rows = []
for r in R22:
    if r["regime"] in ("recommended", "recommended_budget_prov"):
        rows.append(dict(arm=r["regime"], pop=r["pop"], D=r["deadline_s"], seed=r["seed"], agent=r["agent"], runaway=r["runaway"],
                         bad=r["bad"], bad_killed=r["bad_killed"], burn_static=r["burn_static"], burn_arm=r["burn_governed"],
                         cost=100 * r["cost_points"] / 100 if False else r["cost_points"], bound=r["bound_points"]))
for r in R23:
    rows.append(dict(arm="class_kernel", pop=r["pop"], D=r["deadline_s"], seed=r["seed"], agent=r["agent"], runaway=r["runaway"],
                     bad=r["kernel"][2], bad_killed=r["kernel"][3], burn_static=r["burn_static"], burn_arm=r["burn_kernel"],
                     cost=100 * r["kernel"][0] / max(1, r["kernel"][1]), bound=r["bound"]))
for r in rows: r["static_cost"] = 0.0
with open(H / "compare_round23_arms.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
ARMS = ["recommended", "recommended_budget_prov", "class_kernel"]
def agg(arm, pop, D):
    s = [r for r in rows if r["arm"] == arm and r["pop"] == pop and r["D"] == D]
    rr = [r for r in s if r["runaway"]]; hh = [r for r in s if not r["runaway"]]
    bs = sum(r["burn_static"] for r in rr); bg = sum(r["burn_arm"] for r in rr)
    return 100 * (bs - bg) / bs, np.mean([r["cost"] for r in hh]), max(r["cost"] - r["bound"] for r in hh)
print(f"{'pop':5}{'D':>4}  static(saved 0, cost 0) | " + " | ".join(f"{a[:22]:22}" for a in ARMS))
res = {}
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        res[(pop, D)] = {a: agg(a, pop, D) for a in ARMS}
        print(f"{pop:5}{D:>4}  " + " | ".join(f"saved {res[(pop,D)][a][0]:5.1f} cost {res[(pop,D)][a][1]:4.2f}" for a in ARMS))
C1 = all(res[(p, D)]["class_kernel"][0] > res[(p, D)]["recommended_budget_prov"][0] for p in ("wide", "long") for D in (60, 180))
c60 = np.mean([res[(p, 60)]["class_kernel"][1] for p in ("wide", "long")])
C2 = all(res[(p, 60)]["class_kernel"][1] > res[(p, 60)]["recommended_budget_prov"][1] for p in ("wide", "long")) and c60 <= 1.5
# paired by (pop, seed) at 60 s: runaway time saved per seed
def per_seed(arm, pop, D):
    out = {}
    for sd in sorted({r["seed"] for r in rows if r["pop"] == pop and r["D"] == D}):
        rr = [r for r in rows if r["arm"] == arm and r["pop"] == pop and r["D"] == D and r["seed"] == sd and r["runaway"]]
        bs = sum(r["burn_static"] for r in rr); out[sd] = (bs - sum(r["burn_arm"] for r in rr)) / bs if bs else 0
    return out
wins = tot = 0
for p in ("wide", "long"):
    a, b = per_seed("class_kernel", p, 60), per_seed("recommended", p, 60)
    for sd in a: wins += a[sd] > b[sd]; tot += 1
C3 = wins / tot >= 0.7
C4 = all(v[a][2] <= 1e-9 for v in res.values() for a in ARMS)
C5 = not all(res[(p, 10)]["class_kernel"][0] > max(res[(p, 10)][a][0] for a in ARMS[:2]) for p in ("wide", "long"))
print(f"\nC1 {C1}\nC2 {C2} (kernel mean cost at 60 s {c60:.2f})\nC3 {C3} (kernel beats whole-agent adaptive in {wins}/{tot} seeds at 60 s = {100*wins/tot:.0f}%)\nC4 {C4}\nC5 {C5}")
