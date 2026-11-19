#!/usr/bin/env python3
"""Purple team stage 3: attacks X1 to X3 on the per-user one-way deadline G. usage: purple3_red.py --fi DIR"""
import argparse, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import freeinference_loader  # noqa
NMIN, M, Q, FLOOR, SLICE = 30, 1.5, 0.99, 0.25, 250
D = 60000.0

def trace_deadlines(lat):
    """Deadline in force at each call index (G, one-way, whole history)."""
    dg = D; hist = []; out = np.empty(len(lat))
    for s in range(0, len(lat), SLICE):
        if len(hist) >= NMIN: dg = min(dg, min(D, max(FLOOR * D, M * float(np.quantile(hist, Q)))))
        for i in range(s, min(len(lat), s + SLICE)):
            out[i] = dg
            if lat[i] <= dg: hist.append(lat[i])
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--fi", required=True); a = ap.parse_args()
    data = freeinference_loader.load_real(a.fi)
    users = sorted(u for u, v in data.items() if len(v[0]) >= 3000)[:20]
    x1 = []; x2 = []; x3 = []
    for u in users:
        base = np.minimum(data[u][0][:3000], D)
        # X1 evasion: attacker calls at 0.95 x the deadline in force, recomputed as it goes
        lat = list(base[:1000]); dl_before = trace_deadlines(np.array(lat))[-1]
        att = []
        for _ in range(1000):
            dcur = trace_deadlines(np.array(lat[-3000:] if False else lat))[-1] if len(att) % 250 == 0 else dcur
            x = 0.95 * dcur; att.append(x); lat.append(x)
        dl_after = trace_deadlines(np.array(lat))[-1]
        x1.append((dl_before, dl_after, max(att) / dl_before, sum(att) / (1000 * dl_before)))
        # X2 poisoning: 30 calls at 0.9 D, then the real stream
        l2 = np.concatenate([np.full(30, 0.9 * D), base[:3000]]); d2 = trace_deadlines(l2)
        clean = trace_deadlines(base[:3000])
        first_tight = next((i for i in range(30, len(l2)) if d2[i] <= clean[i - 30] * 1.0001 and d2[i] < D), None)
        x2.append((first_tight, float(d2[-1]), float(clean[-1]), float(clean[-1]) < D - 1e-6))
        # X3 starvation: 1000 honest, 2000 calls of 10 ms, then the honest stream resumes
        l3 = np.concatenate([base[:1000], np.full(2000, 10.0), base[1000:3000]]); d3 = trace_deadlines(l3)
        resumed = base[1000:3000]; killed = float(np.mean(resumed > d3[3000])) * 100
        bound = 100.0 * float(np.mean(resumed > FLOOR * D))
        x3.append((float(d3[2999]), killed, bound))
    print("X1 evasion: users", len(x1))
    print("  deadline never rose:", all(a_ <= b_ + 1e-6 for b_, a_, _, _ in x1), "| max single attack call / deadline before:", round(max(r[2] for r in x1), 3), "| mean attack burn / (1000 x deadline before):", round(float(np.mean([r[3] for r in x1])), 3))
    t = [r for r in x2 if r[3]]
    print(f"X2 users whose deadline tightens at all on the clean stream: {len(t)} of {len(x2)}; X1 mean deadline before attack / D: {np.mean([r[0] for r in x1]) / D:.3f}")
    print("X2 poisoning: first call index (after the 30 poison calls) where the deadline had tightened:", sorted(r[0] for r in t if r[0] is not None), "| never tightened:", sum(1 for r in t if r[0] is None))
    print("  final deadline vs clean-run deadline equal:", sum(1 for r in t if abs(r[1] - r[2]) < 1e-6), "of", len(t))
    print("X3 starvation: deadline at end of the fast burst / D:", round(float(np.mean([r[0] for r in x3])) / D, 3), "| honest calls killed after, %:", round(float(np.mean([r[1] for r in x3])), 2), "| certificate bound %:", round(float(np.mean([r[2] for r in x3])), 2), "| killed above bound:", sum(1 for r in x3 if r[1] > r[2] + 1e-9))
