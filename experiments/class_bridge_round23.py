#!/usr/bin/env python3
"""Round 23: parity of TrapBridge class deadlines with the round 22 prototype (class_oneway). Protocol: docs/TRACE_TEST_PLAN.md."""
import argparse, json, sys, tempfile, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import class_budget_round21 as c21   # noqa: E402
import mild_runaway_round22 as c22   # noqa: E402
from integrations.stack_traps import TrapBridge, TrapPolicy   # noqa: E402
from src.governance.governor import Governor              # noqa: E402
from src.governance.operators import OperatorRegistry     # noqa: E402

SLICE = c21.SLICE


def bridge_regime(dur_ms, cls, bad, D_ms, name, tmp):
    D = int(D_ms * 1e6)
    ops = OperatorRegistry(iterations=1000)
    g = Governor(store_path=tmp, operators=ops)
    b = TrapBridge(g, defaults={"deadline_ns": D, "tokens_capacity": 4000, "memory_capacity_bytes": 2**29},
                   policy=TrapPolicy(class_deadlines=True, class_min_completions=c21.NMIN, class_floor=c21.FLOOR,
                                     class_margin=c21.MARGIN, class_history=10**9))
    n = len(dur_ms); extra = legit = nbad = kb = 0; bg = bs = 0.0; rises = 0; refresh_s = 0.0; nref = 0
    last = {}
    for s in range(0, n, SLICE):
        t = time.perf_counter(); b.refresh_class_deadlines(name); refresh_s += time.perf_counter() - t; nref += 1
        for (a, ck) in list(b._class_obs):
            cur = b.class_deadline(name, ck)
            if ck in last and cur > last[ck] + 1e-6: rises += 1
            last[ck] = cur
        comp = {}
        for i in range(s, min(n, s + SLICE)):
            c = cls[i]; dl = b.class_deadline(name, c) / 1e6; x = dur_ms[i]
            if bad[i]:
                nbad += 1; bs += min(x, D_ms); bg += min(x, dl)
                if x > dl: kb += 1
                else: comp.setdefault(c, []).append(x * 1e6)
                continue
            legit += 1
            if x > dl: extra += 1 if x <= D_ms else 0
            else: comp.setdefault(c, []).append(x * 1e6)
        for c, v in comp.items(): b.observe_class_durations(name, c, v)
    audited = sum(1 for e in g.audit.entries if "class deadline" in str(e))
    return extra, legit, nbad, kb, bg, bs, rises, b.counts["class_tightened"], audited, refresh_s / max(1, nref)


def job(args):
    pop, D_s, seed, data = args
    users, runaway, use, rng = c21.make_agents(data, pop, seed)
    D_ms = D_s * 1000.0; rows = []
    for u in users:
        lat, cl = data[u][0][:use].copy(), data[u][1][:use]
        n = len(lat); bad = np.zeros(n, bool)
        if u in runaway:
            m = (rng.random(n) < c22.P_MILD) & (np.arange(n) >= c21.HANG_FROM_SLICE * SLICE)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), c22.CAP_AT * D_ms); bad = m
        pe, pl, pn, pk, pg, ps = c22.class_regime(lat, cl, bad, D_ms, True)
        with tempfile.TemporaryDirectory() as tmp:
            be, bl, bn, bk, bg, bs, rises, tight, aud, ref = bridge_regime(lat, cl, bad, D_ms, u, tmp)
        ok = ~bad
        B = 100.0 * float(((lat[ok] > c21.FLOOR * D_ms) & (lat[ok] <= D_ms)).mean())
        rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "agent": u, "runaway": u in runaway, "bound": B,
                     "proto": [pe, pl, pn, pk, pg, ps], "bridge": [be, bl, bn, bk, bg, bs],
                     "rises": rises, "tightened": tight, "audited": aud, "refresh_s": ref})
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "class_bridge_round23.json")); a = ap.parse_args()
    data = c21.load(a.data)
    jobs = [(p, d, c22.SEED_BASE + i, data) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs): rows += r
    json.dump(rows, open(a.out, "w")); print(len(rows), "agent-runs written")
