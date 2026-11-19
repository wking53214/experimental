#!/usr/bin/env python3
"""Round 23 (kernel integration): the Python bridge writes per-slice class limits; the Rust kernel (stack-p3-2, class_replay example)
makes every kill decision with effective_for_class and its own floor; the result is compared with the Python-side decision.
Protocol and predictions S1 to S10: docs/TRACE_TEST_PLAN.md ("Round 23 (kernel integration) pre-registration").

usage: python experiments/class_kernel_replay_round23.py --data TRACE.jsonl.gz --kernel /home/claude/stack-kernel [--seeds 10]"""
import argparse, json, subprocess, sys, tempfile
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


def python_side(x_ns, cls, D_ns, name, tmp, bad):
    ops = OperatorRegistry(iterations=1000)
    g = Governor(store_path=tmp, operators=ops)
    b = TrapBridge(g, defaults={"deadline_ns": D_ns, "tokens_capacity": 4000, "memory_capacity_bytes": 2**29},
                   policy=TrapPolicy(class_deadlines=True, class_min_completions=c21.NMIN, class_floor=c21.FLOOR,
                                     class_margin=c21.MARGIN, class_history=10**9))
    n = len(x_ns); slices, kills = [], []; burn = 0.0
    for s in range(0, n, SLICE):
        b.refresh_class_deadlines(name)
        lim = {c: b.class_deadline(name, c) for c in set(cls[s:s + SLICE])}
        lim = {c: v for c, v in lim.items() if v < D_ns}
        calls = [[cls[i], int(x_ns[i]), int(bad[i])] for i in range(s, min(n, s + SLICE))]
        slices.append({"limits": lim, "calls": calls})
        comp = {}
        for i in range(s, min(n, s + SLICE)):
            dl = b.class_deadline(name, cls[i]); k = x_ns[i] > dl
            kills.append(int(k))
            if bad[i]: burn += min(float(x_ns[i]), dl)
            if not k: comp.setdefault(cls[i], []).append(float(x_ns[i]))
        for c, v in comp.items(): b.observe_class_durations(name, c, v)
    # Independent audit check: count the entries actually in the governor's log for class boundaries, not the bridge's own counter.
    kinds = ("class_deadline_unknown", "class_deadline_held", "class_tighten_not_applied", "signal")
    n_audit = sum(1 for e in g.audit.entries if e["kind"] in kinds and ".class." in str(e["payload"].get("boundary_id", "")))
    counts = dict(b.counts); counts["audit_entries_class"] = n_audit
    return slices, kills, counts, burn


def job(args):
    pop, D_s, seed, data = args
    users, runaway, use, rng = c21.make_agents(data, pop, seed)
    D_ms = D_s * 1000.0; runs, meta = [], {}
    for u in users:
        lat, cl = data[u][0][:use].copy(), data[u][1][:use]
        n = len(lat); bad = np.zeros(n, bool)
        if u in runaway:
            m = (rng.random(n) < c22.P_MILD) & (np.arange(n) >= c21.HANG_FROM_SLICE * SLICE)
            lat[m] = np.minimum(lat[m] * rng.uniform(2, 4, m.sum()), c22.CAP_AT * D_ms); bad = m
        x_ns = (lat * 1e6).astype(np.int64); D_ns = int(D_s * 1e9)
        with tempfile.TemporaryDirectory() as tmp:
            slices, pk, counts, pburn = python_side(x_ns, [str(c) for c in cl], D_ns, u, tmp, bad)
        rid = f"{pop}|{D_s}|{seed}|{u}"
        runs.append({"id": rid, "default_ns": D_ns, "slices": slices})
        ok = ~bad
        B = 100.0 * float(((lat[ok] > c21.FLOOR * D_ms) & (lat[ok] <= D_ms)).mean())
        meta[rid] = {"pop": pop, "deadline_s": D_s, "seed": seed, "agent": u, "runaway": u in runaway, "bound": B,
                     "lat": lat.tolist(), "bad": bad.tolist(), "py": pk, "counts": counts, "pburn": pburn}
    return runs, meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--kernel", required=True)
    ap.add_argument("--seeds", type=int, default=10); ap.add_argument("--out", default=str(ROOT / "experiments" / "class_kernel_replay_round23.json"))
    a = ap.parse_args(); data = c21.load(a.data)
    jobs = [(p, d, c22.SEED_BASE + i, data) for p in c21.POPS for d in c21.DEADLINES_S for i in range(a.seeds)]
    meta = {}
    with tempfile.TemporaryDirectory() as td:
        runs_path = Path(td) / "runs.jsonl"
        with open(runs_path, "w") as f, ProcessPoolExecutor() as ex:
            for runs, m in ex.map(job, jobs):
                for r in runs: f.write(json.dumps(r) + "\n")
                meta.update(m)
        out = subprocess.run(["cargo", "run", "--release", "-q", "--example", "class_replay", "-p", "stack-p3-2", "--", str(runs_path)],
                             cwd=a.kernel, capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        r = json.loads(line); m = meta[r["id"]]; kk = [int(ch) for ch in r["kills"]]
        assert len(kk) == len(m["py"]), r["id"]
        D_ms = m["deadline_s"] * 1000.0
        def side(kills):
            extra = legit = nbad = kb = 0; bg = bs = 0.0
            for x, b, k in zip(m["lat"], m["bad"], kills):
                if b:
                    nbad += 1; bs += min(x, D_ms); kb += k
                else:
                    legit += 1; extra += 1 if (k and x <= D_ms) else 0
            return extra, legit, nbad, kb
        mism = sum(1 for p, k in zip(m["py"], kk) if p != k)
        rows.append({k: m[k] for k in ("pop", "deadline_s", "seed", "agent", "runaway", "bound")} |
                    {"n": len(kk), "mismatch": mism, "py": side(m["py"]), "kernel": side(kk), "below_floor": r["below_floor"],
                     "burn_py": m["pburn"], "burn_kernel": r["burn_gov"], "burn_static": r["burn_static"],
                     "decisions": m["counts"].get("class_decisions", 0), "audited": m["counts"].get("audit_entries_class", 0),
                     "audit_failed": m["counts"].get("audit_write_failed", 0)})
    json.dump(rows, open(a.out, "w")); print(len(rows), "agent-runs written")
