#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
import sys
R = json.load(open(Path(__file__).with_name(sys.argv[1] if len(sys.argv) > 1 else "class_kernel_replay_round23.json")))
n = sum(r["n"] for r in R); mm = sum(r["mismatch"] for r in R)
print(f"calls {n}, decision mismatches {mm} ({100*mm/n:.5f}%), runs with any mismatch {sum(1 for r in R if r['mismatch'])}/{len(R)}, below_floor hits {sum(r['below_floor'] for r in R)}")
def agg(rows, key):
    rr = [r for r in rows if r["runaway"]]; hh = [r for r in rows if not r["runaway"]]
    bs = sum(r["burn_static"] for r in rr); bg = sum(r[key] for r in rr)
    cost = np.mean([100 * (r["kernel" if key == "burn_kernel" else "py"][0]) / max(1, r["kernel" if key == "burn_kernel" else "py"][1]) for r in hh])
    return 100 * (bs - bg) / bs, cost
print("pop D  py_saved kernel_saved  py_cost kernel_cost")
worst_s = worst_c = 0.0; c60 = []
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        rows = [r for r in R if r["pop"] == pop and r["deadline_s"] == D]
        ps, pc = agg(rows, "burn_py"); ks, kc = agg(rows, "burn_kernel")
        worst_s = max(worst_s, abs(ps - ks)); worst_c = max(worst_c, abs(pc - kc))
        if D == 60: c60.append(kc)
        print(f"{pop:5}{D:>4} {ps:8.2f} {ks:12.2f} {pc:8.2f} {kc:11.2f}")
viol = sum(1 for r in R if not r["runaway"] and 100 * r["kernel"][0] / max(1, r["kernel"][1]) > r["bound"] + 1e-9)
dec = sum(r["decisions"] for r in R); aud = sum(r["audited"] for r in R); fail = sum(r["audit_failed"] for r in R)
print(f"\nS5 mismatch share {100*mm/n:.4f}% (need <= 0.1%) -> {100*mm/n <= 0.1}; max saving gap {worst_s:.3f} (<= 0.1) -> {worst_s <= 0.1}; max cost gap {worst_c:.3f} (<= 0.05) -> {worst_c <= 0.05}")
print(f"S6 kernel honest cost at 60 s (wide, long): {c60[0]:.2f}, {c60[1]:.2f}; mean {np.mean(c60):.2f} -> {np.mean(c60) <= 1.5}")
print(f"S7 class decisions {dec}, audited {aud}, audit failures {fail} -> {dec == aud}")
print(f"certificate violations through the kernel path: {viol}")
