#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
R = json.load(open(Path(__file__).with_name("lag_round25.json")))
def cost(D, mode, f, shifters_only, pops=("wide", "long")):
    rows = [r for r in R if r["deadline_s"] == D and r["mode"] == mode and r["f"] == f and r["pop"] in pops and (r["shifter"] or f == 1.0) and (r["shifter"] == shifters_only or f > 1)]
    return float(np.mean([100 * r["extra"] / max(1, r["legit"]) for r in rows]))
print("D f mode cost_post_shift (shifters; f=1 row = same agents, no shift)")
for D in (10, 60, 180):
    for f in (1.0, 1.5, 2.0, 3.0):
        print(f"{D:>4} {f:3} " + "  ".join(f"{m}={cost(D, m, f, True):5.2f}" for m in ("oneway", "twoway", "reset")))
viol = sum(1 for r in R if 100 * r["extra"] / max(1, r["legit"]) > r["bound"] + 1e-9)
base = cost(60, "oneway", 1.0, True); o2 = cost(60, "oneway", 2.0, True); t2 = cost(60, "twoway", 2.0, True); r2 = cost(60, "reset", 2.0, True)
tb = cost(60, "twoway", 1.0, True)
p5 = (cost(60, "oneway", 3.0, True) - base) > (cost(60, "oneway", 1.5, True) - base)
print(f"\nP1 bound violations (agent-runs) {viol} -> {viol == 0}")
print(f"P2 {o2-base:.2f} pts above unshifted -> {o2-base >= 2}\nP3 twoway {t2:.2f} vs unshifted {tb:.2f} -> {abs(t2-tb) <= 0.5}")
print(f"P4 reset {r2:.2f} vs twoway {t2:.2f} -> {abs(r2-t2) <= 0.5}\nP5 f3 price > f1.5 price -> {p5}")
