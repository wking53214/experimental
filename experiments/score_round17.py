#!/usr/bin/env python3
"""Prints the round 17 tables and verdicts F1-F8 from real_trace_round17_{real,sim}.json."""
import json
from pathlib import Path
import numpy as np
H = Path(__file__).resolve().parent
rows = json.load(open(H / "real_trace_round17_real.json"))["rows"]
sim = json.load(open(H / "real_trace_round17_sim.json"))["results"]
IN = ("insample_long", "insample_wide"); HO = ("holdout_H1", "holdout_H2")
def sel(pops, **k): return [r for r in rows if r["pop"] in pops and all(r[a] == b for a, b in k.items())]
print("F1 violations:", sum(1 for r in rows if r["cost_points"] > r["bound_points"] + 1e-9), "of", len(rows))
for pops, nm in ((IN, "in-sample"), (HO, "hold-out")):
    for pol in ("recommended", "recommended_budget", "recommended_slice", "recommended_slice_budget"):
        xs = sel(pops, policy=pol)
        print(f"F2 {nm:<9} {pol:<24} cells<=1.25pts {100*np.mean([x['cost_points']<=1.25 for x in xs]):5.1f}% (n={len(xs)}) mean cost {np.mean([x['cost_points'] for x in xs]):.2f} max {max(x['cost_points'] for x in xs):.2f}")
print()
for pops, nm in ((IN, "in-sample"), (HO, "hold-out")):
    a = sel(pops, policy="recommended", deadline_s=10); b = sel(pops, policy="recommended_budget", deadline_s=10)
    ma, mb = np.mean([x["cost_points"] for x in a]), np.mean([x["cost_points"] for x in b])
    print(f"F3/F4 {nm:<9} 10s: recommended {ma:.2f} -> budget {mb:.2f} ({100*(1-mb/ma) if ma else 0:.0f}% cut); tightened {100*np.mean([x['tightened'] for x in a]):.0f}% -> {100*np.mean([x['tightened'] for x in b]):.0f}%; "
          f"mean final ratio {np.mean([x['final_deadline_ratio'] for x in a]):.3f} -> {np.mean([x['final_deadline_ratio'] for x in b]):.3f}")
for d in (60, 180):
    for pops, nm in ((IN, "in-sample"), (HO, "hold-out")):
        a = sel(pops, policy="recommended", deadline_s=d); b = sel(pops, policy="recommended_budget", deadline_s=d)
        print(f"      {nm:<9} {d}s: {np.mean([x['cost_points'] for x in a]):.2f} -> {np.mean([x['cost_points'] for x in b]):.2f}")
print()
v1 = sim["v1"]; bk = sim["book_v1"]
for pol in ("recommended", "recommended_budget"):
    h = {a: o for a, o in v1[pol].items() if not o["should_tighten"]}
    worst = max(h.items(), key=lambda kv: kv[1]["legit_cost_points_mean"])
    print(f"F5 v1 {pol:<20} honest agents {len(h)}; largest mean cost {worst[1]['legit_cost_points_mean']:.2f} ({worst[0]}); pooled mean {np.mean([o['legit_cost_points_mean'] for o in h.values()]):.2f}")
for pol in ("recommended", "recommended_budget"):
    r = {a: o["saved_share"] for a, o in v1[pol].items() if o["profile"] == "runaway" and o["saved_share"] is not None}
    print(f"F6 v1 {pol:<20} sustained runaways ({len(r)}): mean saved {100*np.mean(list(r.values())):.1f}%  {({k: round(100*v,1) for k,v in r.items()})}")
print("F7 book_v1 pulse agents, saved share recommended -> recommended_budget:")
for a, o in bk["recommended"].items():
    if o["profile"] == "attacker_pulse":
        b = bk["recommended_budget"][a]
        print(f"     {a:<14} {100*(o['saved_share'] or 0):5.1f}% -> {100*(b['saved_share'] or 0):5.1f}%   (slice policies: {100*(bk['recommended_slice'][a]['saved_share'] or 0):5.1f}% -> {100*(bk['recommended_slice_budget'][a]['saved_share'] or 0):5.1f}%)")
for pol in ("recommended_slice", "recommended_slice_budget"):
    h = [o["net_burn_ns_mean"] for o in bk[pol].values() if not o["should_tighten"]]
    print(f"F8 book_v1 {pol:<24} honest pool mean net burn {np.mean(h)/1e9:.2f} s per agent-run (n agents={len(h)})")
print("\nall simulated agents, saved share and cost, recommended vs recommended_budget (v1):")
for a, o in v1["recommended"].items():
    b = v1["recommended_budget"][a]
    f = lambda x: "  -- " if x is None else f"{100*x:5.1f}"
    print(f"  {a:<16} {o['profile']:<16} saved {f(o['saved_share'])} -> {f(b['saved_share'])} | cost {o['legit_cost_points_mean']:5.2f} -> {b['legit_cost_points_mean']:5.2f}")
