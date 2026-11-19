#!/usr/bin/env python3
"""Purple team stage 2: design/test split, candidates G, C, H1, H2, H3 on real timeouts. usage: purple2_pick.py --fi DIR"""
import argparse, json, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import freeinference_loader  # noqa
NMIN, Q, FLOOR, SLICE = 30, 0.99, 0.25, 250

def run(lat, cls, bad, D, arm):
    margin = 1.2 if arm == "H3" else 1.5
    dg = D; dc = {}; allc = []; compc = defaultdict(list); extra = legit = 0; bs = bg = 0.0
    for s in range(0, len(lat), SLICE):
        if len(allc) >= NMIN: dg = min(dg, min(D, max(FLOOR * D, margin * float(np.quantile(allc, Q)))))
        for k, obs in compc.items():
            if len(obs) >= NMIN: dc[k] = min(dc.get(k, D), min(D, max(FLOOR * D, margin * float(np.quantile(obs, Q)))))
        for i in range(s, min(len(lat), s + SLICE)):
            k = cls[i]; x = lat[i]; warm = k in dc
            if arm in ("G", "H3"): d = dg
            elif arm == "C": d = dc.get(k, D)
            elif arm == "H1": d = min(dg, dc.get(k, D))
            else: d = dc[k] if warm else dg          # H2
            if bad[i]:
                bs += min(x, D); bg += min(x, d); continue
            legit += 1
            if x > d:
                if x <= D: extra += 1
            else: allc.append(x); compc[k].append(x)
    return bs, bg, extra, legit

def score(rows, users, D, arm):
    s = [r for r in rows if r["user"] in users and r["D"] == D and r["arm"] == arm]
    bs = sum(r["burn_s"] for r in s); bg = sum(r["burn_g"] for r in s)
    return dict(saved=100 * (bs - bg) / bs, cost=float(np.mean([100 * r["extra"] / max(1, r["legit"]) for r in s])),
                worst=max(100 * r["extra"] / max(1, r["legit"]) - r["bound"] for r in s))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--fi", required=True); ap.add_argument("--out", default=str(ROOT / "experiments" / "purple2_pick.json")); a = ap.parse_args()
    data = freeinference_loader.load_real(a.fi); rows = []
    users = sorted(u for u, v in data.items() if len(v[0]) >= 2000)
    rng = np.random.default_rng(25000); design = set(u for u in users if rng.random() < 0.5); test = set(users) - design
    for u in users:
        lat, cls, oc = (x[:8000] for x in data[u]); bad = oc == "timeout"
        for D_s in (60, 180):
            D = D_s * 1000.0; bound = 100.0 * float(((lat[~bad] > FLOOR * D) & (lat[~bad] <= D)).mean())
            for arm in ("G", "C", "H1", "H2", "H3"):
                bs, bg, ex, lg = run(lat, cls, bad, D, arm)
                rows.append(dict(user=u, D=D_s, arm=arm, burn_s=bs, burn_g=bg, extra=ex, legit=lg, bound=bound, n_bad=int(bad.sum())))
    json.dump(dict(rows=rows, design=sorted(design), test=sorted(test)), open(a.out, "w"))
    print(f"users {len(users)}: design {len(design)}, test {len(test)}; timeout calls design {sum(r['n_bad'] for r in rows if r['user'] in design and r['D']==60 and r['arm']=='G')}, test {sum(r['n_bad'] for r in rows if r['user'] in test and r['D']==60 and r['arm']=='G')}")
    print("DESIGN half"); res = {}
    for D in (60, 180):
        for arm in ("G", "C", "H1", "H2", "H3"):
            res[(D, arm)] = score(rows, design, D, arm); v = res[(D, arm)]; print(f"D={D:>3} {arm:3} saved {v['saved']:5.1f}%  cost {v['cost']:.2f}  cost-bound max {v['worst']:.2f}")
    # pick rule: average saved over the two deadlines among arms with cost <= G + 0.3 at both and no violation
    ok = [arm for arm in ("C", "H1", "H2", "H3") if all(res[(D, arm)]["cost"] <= res[(D, "G")]["cost"] + 0.3 and res[(D, arm)]["worst"] <= 1e-9 for D in (60, 180))]
    pick = max(ok + ["G"], key=lambda arm: (np.mean([res[(D, arm)]["saved"] for D in (60, 180)]), arm == "G")) if ok else "G"
    print("eligible:", ok, "-> pick:", pick); print("TEST half (scored once)")
    for D in (60, 180):
        for arm in sorted({"G", pick, "C"}):
            v = score(rows, test, D, arm); print(f"D={D:>3} {arm:3} saved {v['saved']:5.1f}%  cost {v['cost']:.2f}  cost-bound max {v['worst']:.2f}")
