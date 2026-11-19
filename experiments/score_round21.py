#!/usr/bin/env python3
"""Prints round 21 tables and verdicts K1-K5 from experiments/class_budget_round21.json."""
import json
from pathlib import Path
import numpy as np
rows = json.load(open(Path(__file__).resolve().parent / "class_budget_round21.json"))["rows"]
REG = ("recommended", "recommended_budget_prov", "recommended_slice_budget_prov", "class_oneway", "class_twoway")
def sel(**k): return [r for r in rows if all(r[a] == b for a, b in k.items())]
def saved(xs):
    bs = sum(x["bad_static"] for x in xs); bg = sum(x["bad_governed"] for x in xs)
    return None if bs == 0 else 100 * (1 - bg / bs)
# K1
v_h = [r for r in rows if not r["runaway"] and r["cost_points"] > r["bound_points"] + 1e-9]
v_h2 = [r for r in rows if r["runaway"] and r["cost_points"] > r["bound_points"] + 1e-9]
lim = lambda r: 0.75 if r["regime"].startswith("class") else 0.271
v_s = [r for r in rows if r["runaway"] and r["bad_static"] and 1 - r["bad_governed"] / r["bad_static"] > lim(r) + 1e-9]
print("K1 honest-agent violations:", len(v_h), "| runaway-agent cost violations:", len(v_h2), "| saved-share violations:", len(v_s), "of", len(rows))
print("   highest single-run saved share: cap", round(100 * max(1 - r["bad_governed"] / r["bad_static"] for r in rows if r["runaway"] and r["bad_static"] and not r["regime"].startswith("class")), 2),
      "| class", round(100 * max(1 - r["bad_governed"] / r["bad_static"] for r in rows if r["runaway"] and r["bad_static"] and r["regime"].startswith("class")), 2))
print("\nsaved share of hung time on runaway agents (pooled over seeds); honest cost on honest agents (mean points)")
print(f"{'pop':<5}{'D':>5}  " + "".join(f"{r.replace('recommended','rec'):>30}" for r in REG))
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        line = f"{pop:<5}{D:>4}s  "
        for g in REG:
            sv = saved([r for r in sel(pop=pop, deadline_s=D, regime=g) if r["runaway"]])
            co = np.mean([r["cost_points"] for r in sel(pop=pop, deadline_s=D, regime=g) if not r["runaway"]])
            line += f"{sv:>17.1f}% / {co:>5.2f}p  "
        print(line)
print()
allp = lambda D, g, f: [r for r in rows if r["deadline_s"] == D and r["regime"] == g and f(r)]
s1 = {g: saved(allp(10, g, lambda r: r["runaway"])) for g in REG}
print("K2 D=10s pooled both populations, saved:", {k.replace('recommended','rec'): round(v, 1) for k, v in s1.items()},
      "| class_oneway / rec_budget_prov:", round(s1["class_oneway"] / s1["recommended_budget_prov"], 2))
c1 = {g: np.mean([r["cost_points"] for r in allp(10, g, lambda r: not r["runaway"])]) for g in REG}
print("K3 D=10s honest mean cost:", {k.replace('recommended','rec'): round(v, 2) for k, v in c1.items()})
lo = lambda g: np.mean([r["cost_points"] for r in sel(pop="long", deadline_s=10, regime=g) if not r["runaway"]])
print("K4 long, 10s honest cost oneway", round(lo("class_oneway"), 2), "twoway", round(lo("class_twoway"), 2), "diff", round(lo("class_oneway") - lo("class_twoway"), 2))
print("K5 D=180s class_oneway saved:", round(saved(allp(180, "class_oneway", lambda r: r["runaway"])), 1), "| twoway", round(saved(allp(180, "class_twoway", lambda r: r["runaway"])), 1))
print("\nworst single honest agent-run cost: " + ", ".join(f"{g.replace('recommended','rec')} {max(r['cost_points'] for r in rows if r['regime']==g and not r['runaway']):.1f}" for g in REG))
