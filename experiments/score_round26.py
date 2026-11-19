#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
R = json.load(open(Path(__file__).with_name("heartbeat_round26.json")))
def st(D, mode, r, q):
    rows = [x for x in R if x["deadline_s"] == D and x["mode"] == mode and x["r"] == r and x["q"] == q]
    rr = [x for x in rows if x["runaway"]]; hh = [x for x in rows if not x["runaway"]]
    bs = sum(x["bs"] for x in rr); bg = sum(x["bg"] for x in rr)
    return 100 * (bs - bg) / bs, float(np.mean([100 * x["extra"] / max(1, x["legit"]) for x in hh]))
print("D r q saved% cost")
for D in (10, 60, 180):
    b = st(D, "base", 0.0, 0.0); print(f"{D:>4}  base      {b[0]:6.2f} {b[1]:5.2f}")
    for r in (0.9, 0.6):
        for q in (0.02, 0.1, 0.3):
            s = st(D, "hb", r, q); print(f"{D:>4} {r} {q:4} {s[0]:6.2f} {s[1]:5.2f}")
viol = sum(1 for x in R if not x["runaway"] and 100 * x["extra"] / max(1, x["legit"]) > x["bound"] + 1e-9)
b = st(60, "base", 0.0, 0.0)
q1 = viol == 0; q2 = st(60, "hb", 0.9, 0.02)[0] >= 45; q3 = st(60, "hb", 0.9, 0.3)[0] - b[0] <= 3
q4 = st(60, "hb", 0.9, 0.1)[1] - b[1] >= 1.0; q5 = st(60, "hb", 0.9, 0.02)[1] - b[1] <= 0.3
print(f"\nQ1 violations {viol} -> {q1}\nQ2 {st(60,'hb',0.9,0.02)[0]:.1f}% -> {q2}\nQ3 gain at q=0.3 {st(60,'hb',0.9,0.3)[0]-b[0]:.2f} -> {q3}")
print(f"Q4 cost diff at q=0.1 {st(60,'hb',0.9,0.1)[1]-b[1]:.2f} -> {q4}\nQ5 cost diff at q=0.02 {st(60,'hb',0.9,0.02)[1]-b[1]:.2f} -> {q5}")
