#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
R = json.load(open(Path(__file__).with_name("class_bridge_round23.json")))
def stats(rows, key):
    rr = [r for r in rows if r["runaway"]]; hh = [r for r in rows if not r["runaway"]]
    bs = sum(r[key][5] for r in rr); bg = sum(r[key][4] for r in rr)
    cost = np.mean([100 * r[key][0] / max(1, r[key][1]) for r in hh])
    return 100 * (bs - bg) / max(1e-9, bs), cost
m1 = m2 = 0.0
print("pop D  proto_saved bridge_saved  proto_cost bridge_cost")
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        rows = [r for r in R if r["pop"] == pop and r["deadline_s"] == D]
        ps, pc = stats(rows, "proto"); bs, bc = stats(rows, "bridge")
        m1 = max(m1, abs(ps - bs)); m2 = max(m2, abs(pc - bc))
        print(f"{pop:5}{D:>4} {ps:10.2f} {bs:12.2f} {pc:11.2f} {bc:11.2f}")
rises = sum(r["rises"] for r in R); mism = sum(1 for r in R if r["tightened"] != r["audited"])
viol = sum(1 for r in R if not r["runaway"] and 100 * r["bridge"][0] / max(1, r["bridge"][1]) > r["bound"] + 1e-9)
ref = np.mean([r["refresh_s"] for r in R]) * 1000
print(f"\nM1 max saving gap {m1:.2f} pts -> {m1 <= 1.0}\nM2 max cost gap {m2:.2f} pts -> {m2 <= 0.2}\nM3 deadline rises {rises} -> {rises == 0}")
print(f"M4 bound violations {viol} -> {viol == 0}\nM5 tightenings without audit entry (agent-runs) {mism} -> {mism == 0}\nM6 mean refresh {ref:.2f} ms -> {ref < 5}")
