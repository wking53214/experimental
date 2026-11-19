#!/usr/bin/env python3
"""Round 24: declared expectation as a tighten-only second signal. Protocol: docs/TRACE_TEST_PLAN.md round 24."""
import argparse, json, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import class_budget_round21 as c21   # noqa: E402
import mild_runaway_round22 as c22   # noqa: E402
SEED_BASE, SIGMAS, M = 24000, (0.5, 1.0, 1.5), 3.0
SLICE, MARGIN, FLOOR, NMIN, Q = c21.SLICE, c21.MARGIN, c21.FLOOR, c21.NMIN, c21.Q


def regime(dur, intended, cls, bad, D, mode, decl_noise):
    """mode: 'base' (class deadline only), 'nonstrategic', 'strategic'. Returns extra legit kills, legit, bad, bad killed, burn gov, burn static, min deadline ratio."""
    n = len(dur); deadline, comp = {}, defaultdict(list)
    extra = legit = nbad = kb = 0; bg = bs = 0.0; low = 1.0
    for s in range(0, n, SLICE):
        for c, obs in comp.items():
            if len(obs) >= NMIN:
                tgt = min(D, max(FLOOR * D, MARGIN * float(np.quantile(obs, Q))))
                deadline[c] = min(deadline.get(c, D), tgt)
        for i in range(s, min(n, s + SLICE)):
            c = cls[i]; dl = deadline.get(c, D); x = dur[i]
            if mode != "base":
                base = x if (bad[i] and mode == "strategic") else intended[i]
                d = base * decl_noise[i] if not (bad[i] and mode == "strategic") else base
                dl = max(FLOOR * D, min(dl, M * d))
            low = min(low, dl / D)
            if bad[i]:
                nbad += 1; bs += min(x, D); bg += min(x, dl)
                if x > dl: kb += 1
                else: comp[c].append(x)
                continue
            legit += 1
            if x > dl: extra += 1 if x <= D else 0
            else: comp[c].append(x)
    return extra, legit, nbad, kb, bg, bs, low


def job(args):
    pop, D_s, seed, data = args
    users, runaway, use, rng = c21.make_agents(data, pop, seed)
    D = D_s * 1000.0; rows = []
    for u in users:
        t0, cl = data[u][0][:use].copy(), data[u][1][:use]
        n = len(t0); lat = t0.copy(); bad = np.zeros(n, bool)
        if u in runaway:
            m = (rng.random(n) < c22.P_MILD) & (np.arange(n) >= c21.HANG_FROM_SLICE * SLICE)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), c22.CAP_AT * D); bad = m
        ok = ~bad
        B = 100.0 * float(((lat[ok] > FLOOR * D) & (lat[ok] <= D)).mean())
        for sig in SIGMAS:
            noise = np.exp(rng.normal(0, sig, n))
            for mode in ("base", "nonstrategic", "strategic"):
                if mode == "base" and sig != SIGMAS[0]:
                    continue
                e, l, nb, kb, bg, bs, low = regime(lat, t0, cl, bad, D, mode, noise)
                rows.append({"pop": pop, "deadline_s": D_s, "seed": seed, "sigma": 0.0 if mode == "base" else sig, "mode": mode,
                             "agent": u, "runaway": u in runaway, "extra": e, "legit": l, "bad": nb, "killed": kb,
                             "burn_g": bg, "burn_s": bs, "low": low, "bound": B})
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "experiments" / "declared_round24.json")); a = ap.parse_args()
    data = c21.load(a.data)
    jobs = [(p, d, SEED_BASE + i, data) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(job, jobs): rows += r
    json.dump(rows, open(a.out, "w")); print(len(rows), "rows written")
