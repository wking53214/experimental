#!/usr/bin/env python3
"""Replay a trace CSV through a Python model of the kernel's limit checks and the real trap loop.

`stack_trace_replay.py` runs every slice through the Rust kernel (`governed_workload`). That is the reference.
This file replaces only the kernel with a few lines of Python (deadline, token and memory checks; per-slice
summary) so that traces can be replayed without the Rust toolchain, many seeds can be run, and policy
parameters can be swept. Everything else is the same code: TrapBridge, TrapPolicy, the Governor, the audit log.

The model is checked against the kernel's committed results (`stack_trace_replay_results*.json`) by
`tests/test_trace_sim.py`: identical static totals and identical tightening decisions on all three traces.
Where the two could differ (which check fires first, how quantiles are computed), the model follows the
committed numbers, not a reading of the Rust source. It is a model of the kernel, not the kernel.

usage: python experiments/stack_trace_sim.py [--csv testdata/traces/stack_trace_v1.csv] [--policies a,b]
       [--slice 250] [--out experiments/stack_trace_sim_results.json] [--quiet]
"""
import argparse, csv, json, os, sys, tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.stack_traps import TrapBridge, TrapPolicy, REASON_TO_LIMIT   # noqa: E402
from src.governance.governor import Governor                                     # noqa: E402

POLICIES = {
    "default": lambda: TrapPolicy(),
    "rate": lambda: TrapPolicy(min_rate=0.01),
    "rate_drift": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1),
    "rate_drift_excess": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True),
    "recommended": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True,
                                      reasons=("deadline_exceeded",)),
}
SUFFIX = ("deadline_ns", "tokens_capacity", "memory_capacity_bytes")
LIMIT_TO_REASON = {s: r for r, (s, _f) in REASON_TO_LIMIT.items()}
QUANTILE_POINTS = (50, 75, 90, 95, 99)


def load_trace(csv_path):
    """rows grouped by agent, in seq order, as numpy columns."""
    cols = defaultdict(lambda: defaultdict(list))
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            c = cols[row["agent_id"]]
            c["task_id"].append(row["task_id"]); c["seq"].append(int(row["seq"]))
            c["duration_ns"].append(int(row["duration_ns"])); c["tokens"].append(int(row["tokens_requested"]))
            c["memory"].append(int(row["memory_requested_bytes"])); c["expected"].append(int(row["expected_load"]))
            c["is_bad"].append(int(row["is_bad"]))
    out = {}
    for agent, c in cols.items():
        order = np.argsort(np.array(c["seq"]))
        out[agent] = {k: (np.array(v, dtype=object) if k == "task_id" else np.array(v, dtype=np.int64))[order]
                      for k, v in c.items()}
    return out


def run_slice(agent, rows, start, stop, limits):
    """The kernel's job for one slice: check each task against the limits it read at the start of the slice.

    Deadline first (a task that overruns is killed and burns exactly the deadline), then tokens, then memory
    (a task refused for tokens or memory still burns its own duration in the committed kernel numbers).
    Expected-load tasks still trap, but are not counted as legitimate failures. Completed-duration statistics
    cover the tasks that no check trapped.
    """
    dl, tc, mc = limits["deadline_ns"], limits["tokens_capacity"], limits["memory_capacity_bytes"]
    dur = rows["duration_ns"][start:stop]; tok = rows["tokens"][start:stop]; mem = rows["memory"][start:stop]
    exp = rows["expected"][start:stop]; bad = rows["is_bad"][start:stop]; ids = rows["task_id"][start:stop]
    over = dur > dl
    tok_trap = (~over) & (tok > tc)
    mem_trap = (~over) & (~tok_trap) & (mem > mc)
    trapped = over | tok_trap | mem_trap
    burned = np.where(over, int(dl), dur)
    traps = []
    for i in np.flatnonzero(trapped):
        reason = "deadline_exceeded" if over[i] else ("token_exhausted" if tok_trap[i] else "memory_exceeded")
        ev = {"trap_id": str(ids[i]), "agent_id": agent, "boundary_layer": 3, "trap_reason": reason,
              "trap_outcome": "HALT", "expected_load": bool(exp[i]), "kernel_made": False}
        if over[i]:
            ev["nanoseconds_since_deadline"] = int(dur[i] - dl)
        elif tok_trap[i]:
            ev["tokens_deficit"] = int(tok[i] - tc)
        else:
            ev["memory_requested_bytes"] = int(mem[i])
        traps.append(ev)
    completed = dur[~trapped]
    legit = bad == 0
    summary = {
        "tasks": int(len(dur)), "legit": int(legit.sum()),
        "legit_failed": int((legit & trapped & (exp == 0)).sum()),
        "expected_trapped": int((trapped & (exp == 1)).sum()),
        "bad": int((bad == 1).sum()), "bad_caught": int(((bad == 1) & trapped).sum()),
        "deadline_traps": int(over.sum()), "token_traps": int(tok_trap.sum()), "memory_traps": int(mem_trap.sum()),
        "bad_ns": int(burned[bad == 1].sum()), "total_ns": int(burned.sum()),
        "completed_median_ns": float(np.median(completed)) if len(completed) else None,
        "completed_q": [float(v) for v in np.percentile(completed, QUANTILE_POINTS, method=os.environ.get("QUANTILE_METHOD", "nearest"))] if len(completed) else None,
    }
    return traps, summary


def totals(rows):
    keys = ("tasks", "legit", "legit_failed", "expected_trapped", "bad", "bad_caught", "deadline_traps", "token_traps",
            "memory_traps", "bad_ns", "total_ns")
    return {k: int(sum(r[k] for r in rows)) for k in keys}


def replay(trace, agents, defaults, policies, slice_n=250, tasks_per_agent=None):
    """trace: load_trace() output; agents: list of dicts with agent_id, profile, should_tighten;
    policies: {name: TrapPolicy factory}. Returns the same structure as stack_trace_replay.py writes."""
    names = [ag["agent_id"] for ag in agents]
    n_tasks = tasks_per_agent or len(trace[names[0]]["seq"])
    n_slices = n_tasks // slice_n
    static = {n: [run_slice(n, trace[n], r * slice_n, (r + 1) * slice_n, defaults)[1] for r in range(n_slices)] for n in names}
    results = {"slices": n_slices, "slice_tasks": slice_n, "defaults": defaults, "policies": {}}
    for pname, factory in policies.items():
        gov = Governor(store_path=tempfile.mkdtemp(prefix="sim-"), use_semantic=True)
        bridge = TrapBridge(gov, policy=factory(), defaults=defaults, is_expected=lambda ev: bool(ev.get("expected_load")))
        governed = {n: [] for n in names}; first = {}; trajectory = {n: [] for n in names}
        for r in range(n_slices):
            for n in names:   # fixed order, as the kernel replay ingests
                limits = {}
                for x in SUFFIX:
                    cur = bridge.limit_for(n, LIMIT_TO_REASON[x])
                    limits[x] = float(defaults[x]) if cur is None else cur
                trajectory[n].append(limits["deadline_ns"])
                traps, s = run_slice(n, trace[n], r * slice_n, (r + 1) * slice_n, limits)
                governed[n].append(s)
                bridge.observe_transactions(n, s["tasks"], completed_median_ns=s["completed_median_ns"],
                                            completed_quantiles=s["completed_q"])
                bridge.ingest(traps)
                if n not in first and any((bridge.limit_for(n, LIMIT_TO_REASON[x]) or float(defaults[x])) < float(defaults[x])
                                          for x in SUFFIX):
                    first[n] = r
        per = {}
        for ag in agents:
            n = ag["agent_id"]; limits = {}
            for x in SUFFIX:
                bid = bridge.boundary_id(n, x)
                try:
                    st = gov.boundary_status(bid)
                except KeyError:
                    st = None
                limits[x] = None if st is None else {"limit": st["limit"], "held": st["held"],
                                                     "pending": st["pending_operator_requests"],
                                                     "tightenings": st["automatic_tightenings_since_acknowledgement"]}
            tightened = any(v and v["limit"] < float(defaults[k]) for k, v in limits.items())
            per[n] = {"profile": ag["profile"], "should_tighten": int(ag["should_tighten"]), "tightened": tightened,
                      "first_tightened_slice": first.get(n), "limits": limits,
                      "static": totals(static[n]), "governed": totals(governed[n]),
                      "deadline_trajectory_ns": trajectory[n]}
        results["policies"][pname] = {"agents": per, "bridge_counts": dict(bridge.counts),
                                      "audit_chain_intact": gov.audit.verify()[0]}
    return results


def load_set(csv_path):
    d = Path(csv_path).parent; tag = Path(csv_path).stem.replace("stack_trace_", "")
    manifest = json.loads((d / ("MANIFEST.json" if tag == "v1" else f"MANIFEST_{tag}.json")).read_text())
    agents = list(csv.DictReader(open(d / f"stack_agents_{tag}.csv")))
    return tag, manifest, agents


def score(pr):
    ags = pr["agents"]
    tp = sum(1 for v in ags.values() if v["should_tighten"] and v["tightened"])
    fn = sum(1 for v in ags.values() if v["should_tighten"] and not v["tightened"])
    fp = sum(1 for v in ags.values() if not v["should_tighten"] and v["tightened"])
    tn = sum(1 for v in ags.values() if not v["should_tighten"] and not v["tightened"])
    return tp, fn, fp, tn


def report(res):
    for pname, pr in res["policies"].items():
        tp, fn, fp, tn = score(pr)
        print(f"\n== {pname}: should-tighten agents tightened {tp}/{tp+fn}; should-not agents tightened {fp}/{fp+tn}; "
              f"audit intact {pr['audit_chain_intact']}; counts {pr['bridge_counts']}")
        print("%-20s %-22s %3s %-4s %-9s %-24s %-22s" % ("agent", "profile", "sh", "tgt", "1st slice", "legit timeouts s->g", "bad time s->g (s)"))
        for n, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            print("%-20s %-22s %3d %-4s %-9s %6.2f%% -> %6.2f%%      %8.1f -> %8.1f" % (
                n, v["profile"], v["should_tighten"], "YES" if v["tightened"] else "no", v["first_tightened_slice"],
                100 * s["legit_failed"] / max(1, s["legit"]), 100 * g["legit_failed"] / max(1, g["legit"]),
                s["bad_ns"] / 1e9, g["bad_ns"] / 1e9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "testdata/traces/stack_trace_v1.csv"))
    ap.add_argument("--slice", type=int, default=250)
    ap.add_argument("--policies", default=",".join(POLICIES))
    ap.add_argument("--out", default=None); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    tag, manifest, agents = load_set(a.csv)
    trace = load_trace(a.csv)
    res = replay(trace, agents, manifest["defaults"], {p: POLICIES[p] for p in a.policies.split(",")},
                 slice_n=a.slice, tasks_per_agent=manifest["tasks_per_agent"])
    res.update({"csv": os.path.relpath(a.csv, ROOT), "sha256": manifest["sha256"], "kernel": "python model (stack_trace_sim.py)"})
    out = Path(a.out) if a.out else ROOT / "experiments" / f"stack_trace_sim_results_{tag}.json"
    for pr in res["policies"].values():
        for v in pr["agents"].values():
            v.pop("deadline_trajectory_ns", None)
    out.write_text(json.dumps(res, indent=1))
    if not a.quiet:
        report(res)


if __name__ == "__main__":
    main()
