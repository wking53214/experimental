#!/usr/bin/env python3
import json, sys
from pathlib import Path
import numpy as np
R = json.load(open(Path(__file__).with_name("split_class_round23.json")))
def agg(pop, D, arm):
    s = [r for r in R if r["pop"] == pop and r["D"] == D and r["arm"] == arm]
    rr = [r for r in s if r["runaway"]]; hh = [r for r in s if not r["runaway"]]
    bs = sum(r["burn_s"] for r in rr); bg = sum(r["burn_g"] for r in rr)
    return dict(saved=100 * (bs - bg) / bs, cost=float(np.mean([100 * r["extra"] / max(1, r["legit"]) for r in hh])),
                warm_unc=100 * sum(r["warm_unc"] for r in rr) / bs, worst=max(100 * r["extra"] / max(1, r["legit"]) - r["bound"] for r in hh),
                gt64=100 * float(np.mean([r["classes"] > 64 for r in s])))
print(f"{'pop':5}{'D':>4} {'arm':4}{'saved%':>8}{'cost':>7}{'warm-uncaught%':>16}{'cost-bound max':>16}{'agents>64cls%':>15}")
out = {}
for pop in ("wide", "long"):
    for D in (10, 60, 180):
        for arm in ("A0", "A1", "A2"):
            v = out[(pop, D, arm)] = agg(pop, D, arm)
            print(f"{pop:5}{D:>4} {arm:4}{v['saved']:8.1f}{v['cost']:7.2f}{v['warm_unc']:16.1f}{v['worst']:16.2f}{v['gt64']:15.1f}")
P1 = all(out[(p, 60, "A1")]["saved"] > out[(p, 60, "A0")]["saved"] and out[(p, 60, "A1")]["cost"] <= out[(p, 60, "A0")]["cost"] + 0.5 for p in ("wide", "long"))
P2 = all(out[(p, D, "A2")]["saved"] >= out[(p, D, "A1")]["saved"] and out[(p, D, "A2")]["cost"] <= out[(p, D, "A1")]["cost"] + 0.3 for p in ("wide", "long") for D in (60,))
wu = np.mean([out[(p, 60, "A1")]["warm_unc"] for p in ("wide", "long")])
P3 = wu < 55; P4 = all(v["worst"] <= 1e-9 for v in out.values())
g = np.mean([out[(p, 60, "A1")]["gt64"] for p in ("wide", "long")]); P5 = g >= 20
print(f"\nP1 {P1}\nP2 {P2}\nP3 {P3} (warm-uncaught under A1 at 60 s: {wu:.1f}%)\nP4 {P4}\nP5 {P5} (agents with more than 64 classes under A1 at 60 s: {g:.1f}%)")
