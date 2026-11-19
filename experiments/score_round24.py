#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
R = json.load(open(Path(__file__).with_name("declared_round24.json")))
def st(pop, D, mode, sig):
    rows = [r for r in R if r["pop"] in pop and r["deadline_s"] == D and r["mode"] == mode and r["sigma"] == sig]
    rr = [r for r in rows if r["runaway"]]; hh = [r for r in rows if not r["runaway"]]
    bs = sum(r["burn_s"] for r in rr); bg = sum(r["burn_g"] for r in rr)
    return 100 * (bs - bg) / bs, float(np.mean([100 * r["extra"] / max(1, r["legit"]) for r in hh]))
P2 = ("wide", "long")
print("pop D mode sigma saved% cost")
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        for mode, sg in (("base", 0.0),) + tuple((m, s) for s in (0.5, 1.0, 1.5) for m in ("nonstrategic", "strategic")):
            sv, c = st((pop,), D, mode, sg); print(f"{pop:5}{D:>4} {mode:13}{sg:4} {sv:7.2f} {c:6.2f}")
viol = sum(1 for r in R if not r["runaway"] and 100 * r["extra"] / max(1, r["legit"]) > r["bound"] + 1e-9)
low = min(r["low"] for r in R)
b60 = st(P2, 60, "base", 0.0); n60 = st(P2, 60, "nonstrategic", 0.5)
N2 = n60[0] >= 1.5 * b60[0]
gap = [(st(P2, D, "strategic", s)[0] - st(P2, D, "base", 0.0)[0]) for D in (10, 60, 180) for s in (0.5, 1.0, 1.5)]
N3 = all(-1e-9 <= g <= 0.5 for g in gap)
N4 = st(P2, 60, "nonstrategic", 0.5)[1] - b60[1] <= 0.5
N5 = st(P2, 60, "nonstrategic", 1.5)[1] - b60[1] > 2
N6 = st(("long",), 60, "nonstrategic", 0.5)[0] >= 35
print(f"\nN1 violations {viol}, lowest deadline/D {low:.3f} -> {viol == 0 and low >= 0.25 - 1e-9}")
print(f"N2 {N2} (decl {n60[0]:.1f}% vs base {b60[0]:.1f}%)\nN3 {N3} (strategic minus base: {min(gap):.2f} to {max(gap):.2f})")
print(f"N4 {N4} (cost diff {st(P2,60,'nonstrategic',0.5)[1]-b60[1]:.2f})\nN5 {N5} (cost diff {st(P2,60,'nonstrategic',1.5)[1]-b60[1]:.2f})\nN6 {N6} ({st(('long',),60,'nonstrategic',0.5)[0]:.1f}%)")
