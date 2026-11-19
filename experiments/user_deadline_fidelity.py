#!/usr/bin/env python3
"""Fidelity check of TrapBridge.user_deadline against the reference simulation (purple4_patch.real, windowed). usage: user_deadline_fidelity.py --fi DIR"""
import argparse, sys, tempfile
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import freeinference_loader, purple4_patch as P   # noqa
from integrations.stack_traps import TrapBridge, TrapPolicy   # noqa
from src.governance.governor import Governor              # noqa
from src.governance.operators import OperatorRegistry      # noqa

def bridge_run(lat, bad, D_ms, tmp):
    ops = OperatorRegistry(iterations=1000); g = Governor(store_path=tmp, operators=ops)
    D = D_ms * 1e6
    b = TrapBridge(g, defaults={"deadline_ns": D, "tokens_capacity": 4000, "memory_capacity_bytes": 2**29}, policy=TrapPolicy(user_deadline=True))
    bs = bg = 0.0; extra = legit = 0
    for s in range(0, len(lat), P.SLICE):
        b.refresh_user_deadline("u")
        dl = b.user_deadline("u") / 1e6; comp = []
        for i in range(s, min(len(lat), s + P.SLICE)):
            x = lat[i]
            if bad[i]: bs += min(x, D_ms); bg += min(x, dl); continue
            legit += 1
            if x > dl:
                if x <= D_ms: extra += 1
            else: comp.append(x * 1e6)
        b.observe_user_durations("u", comp)
    n_dec = b.counts["user_decisions"]; logged = sum(1 for e in g.audit.entries if e["kind"] in ("user_deadline_unknown", "user_deadline_held", "user_tighten_not_applied") or (e["kind"] == "signal" and ".class." not in str(e["payload"].get("boundary_id", ""))))
    return bs, bg, extra, legit, n_dec, b.counts["user_tightened"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--fi", required=True); a = ap.parse_args()
    data = freeinference_loader.load_real(a.fi)
    for Ds in (60, 180):
        D = Ds * 1000.0; R = dict(rb=[0, 0, 0, 0], ref=[0, 0, 0, 0]); worst = -1e9; viol = 0
        for u, (lat, cls, oc) in data.items():
            if len(lat) < 2000: continue
            lat, oc = lat[:8000], oc[:8000]; bad = oc == "timeout"
            with tempfile.TemporaryDirectory() as tmp:
                bs, bg, ex, lg, nd, nt = bridge_run(lat, bad, D, tmp)
            rbs, rbg, rex, rlg = P.real(lat, bad, D, P.W)
            for k, v in zip(range(4), (bs, bg, ex, lg)): R["rb"][k] += v
            for k, v in zip(range(4), (rbs, rbg, rex, rlg)): R["ref"][k] += v
            bound = 100.0 * float(((lat[~bad] > 0.25 * D) & (lat[~bad] <= D)).mean()); c = 100 * ex / max(1, lg); worst = max(worst, c - bound)
        sv = lambda r: 100 * (r[0] - r[1]) / r[0]; co = lambda r: 100 * r[2] / r[3]
        print(f"D={Ds:>3}: bridge saved {sv(R['rb']):5.1f}% cost(pooled) {co(R['rb']):.2f} | reference saved {sv(R['ref']):5.1f}% cost(pooled) {co(R['ref']):.2f} | cost-bound max (bridge) {worst:.2f}")
