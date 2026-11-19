#!/usr/bin/env python3
"""Purple team stage 4: windowed G (W = 1000 recent completions) against poisoning and on real timeouts. usage: purple4_patch.py --fi DIR"""
import argparse, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import freeinference_loader  # noqa
NMIN, M, Q, FLOOR, SLICE, W = 30, 1.5, 0.99, 0.25, 250, 1000

def deadlines(lat, D, window):
    dg = D; hist = []; out = np.empty(len(lat))
    for s in range(0, len(lat), SLICE):
        h = hist[-window:] if window else hist
        if len(h) >= NMIN: dg = min(dg, min(D, max(FLOOR * D, M * float(np.quantile(h, Q)))))
        for i in range(s, min(len(lat), s + SLICE)):
            out[i] = dg
            if lat[i] <= dg: hist.append(lat[i])
    return out

def real(lat, bad, D, window):
    d = deadlines(np.where(bad, np.inf, lat), D, window) if False else None
    dg = D; hist = []; bs = bg = 0.0; extra = legit = 0
    for s in range(0, len(lat), SLICE):
        h = hist[-window:] if window else hist
        if len(h) >= NMIN: dg = min(dg, min(D, max(FLOOR * D, M * float(np.quantile(h, Q)))))
        for i in range(s, min(len(lat), s + SLICE)):
            x = lat[i]
            if bad[i]: bs += min(x, D); bg += min(x, dg); continue
            legit += 1
            if x > dg:
                if x <= D: extra += 1
            else: hist.append(x)
    return bs, bg, extra, legit

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--fi", required=True); a = ap.parse_args()
    data = freeinference_loader.load_real(a.fi); D = 60000.0
    users = sorted(u for u, v in data.items() if len(v[0]) >= 3000)[:20]; res = []
    for u in users:
        base = np.minimum(data[u][0][:3000], D)
        if deadlines(base, D, None)[-1] >= D - 1e-6: continue
        l2 = np.concatenate([np.full(30, 0.9 * D), base]); dp = deadlines(l2, D, W); dn = deadlines(l2, D, None)
        t = next((i - 30 for i in range(30, len(l2)) if dp[i] < D - 1e-6), None); tn = next((i - 30 for i in range(30, len(l2)) if dn[i] < D - 1e-6), None)
        res.append((t, tn))
    print("B1 poisoned users that tighten on the clean stream:", len(res), "| first tightening index with window:", [r[0] for r in res], "| without window:", [r[1] for r in res])
    for Ds in (60, 180):
        out = {}
        for name, win in (("G", None), ("Gw", W)):
            bs = bg = 0.0; costs = []; worst = -1e9
            for u, (lat, cls, oc) in data.items():
                if len(lat) < 2000: continue
                lat, oc = lat[:8000], oc[:8000]; bad = oc == "timeout"; Dm = Ds * 1000.0
                b, g, ex, lg = real(lat, bad, Dm, win); bs += b; bg += g; c = 100 * ex / max(1, lg); costs.append(c)
                bound = 100.0 * float(((lat[~bad] > FLOOR * Dm) & (lat[~bad] <= Dm)).mean()); worst = max(worst, c - bound)
            out[name] = (100 * (bs - bg) / bs, float(np.mean(costs)), worst)
        print(f"D={Ds:>3}: G saved {out['G'][0]:5.1f}% cost {out['G'][1]:.2f} | Gw saved {out['Gw'][0]:5.1f}% cost {out['Gw'][1]:.2f} | cost-bound max G {out['G'][2]:.2f}, Gw {out['Gw'][2]:.2f}")
