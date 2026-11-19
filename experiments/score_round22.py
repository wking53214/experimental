#!/usr/bin/env python3
"""Score round 22 predictions L1 to L6 from mild_runaway_round22.json."""
import json, sys
from pathlib import Path
import numpy as np
import sys
R = json.load(open(Path(__file__).with_name(sys.argv[1] if len(sys.argv) > 1 else "mild_runaway_round22.json")))["rows"]
REG = ["recommended", "recommended_budget_prov", "recommended_slice_budget_prov", "class_oneway", "class_twoway"]

def sel(**k):
    return [r for r in R if all(r[a] == b for a, b in k.items())]

def agg(rows):
    rr = [r for r in rows if r["runaway"]]; hh = [r for r in rows if not r["runaway"]]
    bad = sum(r["bad"] for r in rr); kill = sum(r["bad_killed"] for r in rr)
    bs = sum(r["burn_static"] for r in rr); bg = sum(r["burn_governed"] for r in rr)
    return {"kill%": 100 * kill / max(1, bad), "saved%": 100 * (bs - bg) / max(1e-9, bs),
            "cost": float(np.mean([r["cost_points"] for r in hh])) if hh else float("nan"),
            "worst": max([r["cost_points"] - r["bound_points"] for r in hh] or [0])}

out = {}
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        for g in REG:
            out[(pop, D, g)] = agg(sel(pop=pop, deadline_s=D, regime=g))
print(f"{'pop':5}{'D':>5} {'regime':30}{'kill%':>8}{'saved%':>8}{'cost':>7}{'cost-bound max':>16}")
for k, v in out.items():
    print(f"{k[0]:5}{k[1]:>5} {k[2]:30}{v['kill%']:8.1f}{v['saved%']:8.1f}{v['cost']:7.2f}{v['worst']:16.2f}")

def both(D, g, key):
    return float(np.mean([out[(p, D, g)][key] for p in ("wide", "long")]))
cap = ["recommended", "recommended_budget_prov", "recommended_slice_budget_prov"]
L1 = all(v["worst"] <= 1e-9 for v in out.values())
L1b = all(out[(p, D, g)]["saved%"] <= 27.1 + 1e-6 for p in ("wide", "long") for D in (10, 60, 180) for g in cap)
L2 = both(60, "class_oneway", "kill%") >= 10 and max(both(60, g, "kill%") for g in cap) <= 3
a, b = both(60, "class_oneway", "saved%"), both(60, "recommended_budget_prov", "saved%")
L3 = a >= 3 * b and b < 5
L4 = both(60, "class_oneway", "cost") <= 2.0
L5 = out[("long", 60, "class_oneway")]["saved%"] - out[("long", 60, "class_twoway")]["saved%"] >= 2
L6 = all(out[(p, D, "class_oneway")]["saved%"] < 25 for p in ("wide", "long") for D in (10, 60, 180))
print(f"\nL1 cert (honest cost <= bound): {L1}; cap saving <= 27.1: {L1b}\nL2 {L2} (class {both(60,'class_oneway','kill%'):.1f}%, cap max {max(both(60,g,'kill%') for g in cap):.1f}%)")
print(f"L3 {L3} (class {a:.1f}% vs {b:.1f}%)\nL4 {L4} ({both(60,'class_oneway','cost'):.2f})\nL5 {L5}\nL6 {L6}")
