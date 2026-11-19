#!/usr/bin/env python3
"""Purple team stage 1 (docs/TRACE_TEST_PLAN.md): shipped class deadlines against REAL timeout calls. usage: purple1_real_timeouts.py --fi DIR"""
import argparse, json, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import freeinference_loader  # noqa
NMIN, MARGIN, Q, FLOOR, SLICE = 30, 1.5, 0.99, 0.25, 250

def run(lat, cls, bad, D, by_class):
    dl = {}; comp = defaultdict(list); extra = legit = 0; bs = bg = 0.0
    for s in range(0, len(lat), SLICE):
        for k, obs in comp.items():
            if len(obs) >= NMIN:
                t = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q)))); dl[k] = min(dl.get(k, D), t)
        for i in range(s, min(len(lat), s + SLICE)):
            k = cls[i] if by_class else "_"; d = dl.get(k, D); x = lat[i]
            if bad[i]:
                bs += min(x, D); bg += min(x, d); continue
            legit += 1
            if x > d:
                if x <= D: extra += 1
            else: comp[k].append(x)
    return bs, bg, extra, legit

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--fi", required=True); ap.add_argument("--out", default=str(ROOT / "experiments" / "purple1_real_timeouts.json")); a = ap.parse_args()
    data = freeinference_loader.load_real(a.fi); rows = []
    for u, (lat, cls, oc) in data.items():
        if len(lat) < 2000: continue
        lat, cls, oc = lat[:8000], cls[:8000], oc[:8000]; bad = oc == "timeout"; honest = ~bad
        for D_s in (60, 180):
            D = D_s * 1000.0; ok = honest; bound = 100.0 * float(((lat[ok] > FLOOR * D) & (lat[ok] <= D)).mean())
            for arm, bc in (("C", True), ("G", False)):
                bs, bg, ex, lg = run(lat, cls, bad, D, bc)
                rows.append(dict(user=u, D=D_s, arm=arm, burn_s=bs, burn_g=bg, extra=ex, legit=lg, bound=bound, n_bad=int(bad.sum()), n=len(lat)))
    json.dump(rows, open(a.out, "w")); print(len(rows), "rows,", len({r['user'] for r in rows}), "users,", sum(r['n_bad'] for r in rows if r['D'] == 60 and r['arm'] == 'C'), "timeout calls")
    for D in (60, 180):
        for arm in ("C", "G"):
            s = [r for r in rows if r["D"] == D and r["arm"] == arm]; bs = sum(r["burn_s"] for r in s); bg = sum(r["burn_g"] for r in s)
            cost = float(np.mean([100 * r["extra"] / max(1, r["legit"]) for r in s])); worst = max(100 * r["extra"] / max(1, r["legit"]) - r["bound"] for r in s)
            print(f"D={D:>3} arm {arm}: timeout time saved {100*(bs-bg)/bs:5.1f}%  honest cost {cost:.2f}  cost-bound max {worst:.2f}")
