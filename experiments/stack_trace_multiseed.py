#!/usr/bin/env python3
"""Many seeds and a parameter sweep for the trap policies, through the Python model of the kernel.

The committed trace replays (`docs/TRACE_TEST_PLAN.md`) are single runs of single seeds. This regenerates the same
three rosters (`experiments/gen_trace.py`) under fresh seeds, replays each through `stack_trace_sim.replay`, and
reports, per agent profile, how often each policy tightens, when, and what it costs and saves. The sweep varies
the recommended policy's two thresholds (min_rate, drift_factor) on a subset of seeds.

Predictions for this run were written in docs/TRACE_TEST_PLAN.md before it was executed.

usage: python experiments/stack_trace_multiseed.py [--seeds 20] [--sweep-seeds 10] [--workers 8] [--no-sweep]
       [--seed-base 1000] [--policies recommended] [--rosters v1,inflate_v1,holdout_v1] [--tag _fresh]
writes experiments/stack_trace_multiseed_results<tag>.json and experiments/stack_trace_sweep_results<tag>.json
"""
import argparse, json, os, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "experiments"))
import gen_trace                                   # noqa: E402
import stack_trace_sim as sim                      # noqa: E402
from integrations.stack_traps import TrapPolicy   # noqa: E402

DEFAULTS = {"deadline_ns": gen_trace.DEADLINE_NS, "tokens_capacity": gen_trace.TOKENS_CAP,
            "memory_capacity_bytes": gen_trace.MEM_CAP}
SEED_BASE = 1000      # seeds 1001.. ; the committed traces used 20261009 and 777
TASKS = 8000
MAIN_POLICIES = ("default", "rate", "rate_drift", "recommended")
SWEEP_RATES = (0.005, 0.01, 0.02)
SWEEP_DRIFT = (1.3, 1.5, 2.0)
COL = {h: i for i, h in enumerate(gen_trace.HEADER)}


def make_trace(roster_name, seed):
    """Generate a roster under a seed, in memory, in the shape stack_trace_sim.load_trace returns."""
    roster = gen_trace.ROSTERS[roster_name]
    seeds = np.random.SeedSequence(seed).spawn(len(roster))
    trace, agents = {}, []
    for idx, ((name, profile, st, team, desc, prm), ss) in enumerate(zip(roster, seeds)):
        rows = gen_trace.gen_agent(name, profile, prm, TASKS, np.random.default_rng(ss), idx, team)
        trace[name] = {
            "task_id": np.array([r[COL["task_id"]] for r in rows], dtype=object),
            "seq": np.array([r[COL["seq"]] for r in rows], dtype=np.int64),
            "duration_ns": np.array([r[COL["duration_ns"]] for r in rows], dtype=np.int64),
            "tokens": np.array([r[COL["tokens_requested"]] for r in rows], dtype=np.int64),
            "memory": np.array([r[COL["memory_requested_bytes"]] for r in rows], dtype=np.int64),
            "expected": np.array([r[COL["expected_load"]] for r in rows], dtype=np.int64),
            "is_bad": np.array([r[COL["is_bad"]] for r in rows], dtype=np.int64),
        }
        agents.append({"agent_id": name, "profile": profile, "should_tighten": st, "team": team})
    return trace, agents


def sweep_policy(min_rate, drift_factor):
    return lambda: TrapPolicy(min_rate=min_rate, drift_factor=drift_factor, drift_recent_reports=1, drift_excess=True,
                              reasons=("deadline_exceeded",))


def one_job(job):
    """(roster, seed, {policy_name: (kind, args)}) -> compact per-agent outcomes."""
    roster, seed, specs = job
    trace, agents = make_trace(roster, seed)
    policies = {}
    for pname, (kind, args) in specs.items():
        policies[pname] = sim.POLICIES[pname] if kind == "named" else sweep_policy(*args)
    res = sim.replay(trace, agents, DEFAULTS, policies, slice_n=250, tasks_per_agent=TASKS)
    out = {}
    for pname, pr in res["policies"].items():
        out[pname] = {}
        for agent, v in pr["agents"].items():
            s, g = v["static"], v["governed"]
            out[pname][agent] = {
                "profile": v["profile"], "should_tighten": v["should_tighten"], "tightened": v["tightened"],
                "first": v["first_tightened_slice"],
                "legit_cost_points": 100.0 * (g["legit_failed"] - s["legit_failed"]) / max(1, s["legit"]),
                "legit_fail_rate_static": 100.0 * s["legit_failed"] / max(1, s["legit"]),
                "bad_ns_static": s["bad_ns"], "bad_ns_governed": g["bad_ns"],
                "final_deadline_ns": v["limits"]["deadline_ns"]["limit"] if v["limits"]["deadline_ns"] else DEFAULTS["deadline_ns"],
                "verdicts": v.get("verdicts", []), "restore_requested": v.get("restore_requested", False),
            }
        out[pname]["_audit_intact"] = pr["audit_chain_intact"] and pr.get("integrity_ok", True)
    return roster, seed, out


def summarize(runs):
    """runs: list of (roster, seed, {policy: {agent: outcome}}) -> per (roster, policy, agent) aggregates."""
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for roster, seed, out in runs:
        for pname, per in out.items():
            for agent, o in per.items():
                if agent.startswith("_"):
                    continue
                a = agg[roster][pname][agent]
                a.append(o)
    summary = {}
    for roster, pols in agg.items():
        summary[roster] = {}
        for pname, agents in pols.items():
            rows = {}
            for agent, outs in agents.items():
                n = len(outs)
                tight = [o["tightened"] for o in outs]
                firsts = [o["first"] for o in outs if o["first"] is not None]
                bad_s = sum(o["bad_ns_static"] for o in outs); bad_g = sum(o["bad_ns_governed"] for o in outs)
                firsts_v = [o["verdicts"][0]["verdict"] for o in outs if o.get("verdicts")]
                rows[agent] = {
                    "first_verdict": {k: firsts_v.count(k) for k in ("IMPROVED", "UNCHANGED", "DEGRADED", "UNKNOWN") if firsts_v.count(k)},
                    "any_degraded_seeds": int(sum(any(x["verdict"] == "DEGRADED" for x in o.get("verdicts", [])) for o in outs)),
                    "degraded_while_bad_seeds": int(sum(any(x["verdict"] == "DEGRADED" and x["bad_in_window"] > 0 for x in o.get("verdicts", [])) for o in outs)),
                    "restore_requested_seeds": int(sum(bool(o.get("restore_requested")) for o in outs)),
                    "profile": outs[0]["profile"], "should_tighten": outs[0]["should_tighten"], "seeds": n,
                    "tightened_seeds": int(sum(tight)), "tightened_share": sum(tight) / n,
                    "first_slice_median": (float(np.median(firsts)) if firsts else None),
                    "first_slice_min_max": ([min(firsts), max(firsts)] if firsts else None),
                    "legit_fail_rate_static_mean": float(np.mean([o["legit_fail_rate_static"] for o in outs])),
                    "legit_cost_points_mean": float(np.mean([o["legit_cost_points"] for o in outs])),
                    "legit_cost_points_max": float(np.max([o["legit_cost_points"] for o in outs])),
                    "bad_time_saved_share": (None if bad_s == 0 else 1.0 - bad_g / bad_s),
                    "bad_time_saved_min_max": (None if bad_s == 0 else [
                        min(1 - o["bad_ns_governed"] / o["bad_ns_static"] for o in outs if o["bad_ns_static"]),
                        max(1 - o["bad_ns_governed"] / o["bad_ns_static"] for o in outs if o["bad_ns_static"])]),
                }
            summary[roster][pname] = rows
    return summary


def print_summary(summary, title):
    print(f"\n######## {title}")
    for roster, pols in summary.items():
        for pname, rows in pols.items():
            fp = sum(r["tightened_seeds"] for r in rows.values() if not r["should_tighten"])
            fp_n = sum(r["seeds"] for r in rows.values() if not r["should_tighten"])
            tp = sum(r["tightened_seeds"] for r in rows.values() if r["should_tighten"])
            tp_n = sum(r["seeds"] for r in rows.values() if r["should_tighten"])
            print(f"\n== {roster} / {pname}: should-tighten agent-seeds tightened {tp}/{tp_n}; should-NOT agent-seeds tightened {fp}/{fp_n}")
            print("%-20s %-24s %2s %5s %7s %8s %8s %9s %9s  %s" % ("agent", "profile", "sh", "tight", "1st med", "static%", "cost pts", "cost max", "saved", "verdicts: first / any DEGRADED / DEGRADED while bad / restore"))
            for agent, r in rows.items():
                print("%-20s %-24s %2d %5s %7s %8.2f %8.2f %9.2f %9s  %s" % (
                    agent, r["profile"], r["should_tighten"], f"{r['tightened_seeds']}/{r['seeds']}",
                    "-" if r["first_slice_median"] is None else f"{r['first_slice_median']:.0f}",
                    r["legit_fail_rate_static_mean"], r["legit_cost_points_mean"], r["legit_cost_points_max"],
                    "-" if r["bad_time_saved_share"] is None else f"{100 * r['bad_time_saved_share']:.0f}%",
                    "" if not r["first_verdict"] else f"{r['first_verdict']} / {r['any_degraded_seeds']} / {r['degraded_while_bad_seeds']} / {r['restore_requested_seeds']}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20); ap.add_argument("--sweep-seeds", type=int, default=10)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4); ap.add_argument("--no-sweep", action="store_true")
    ap.add_argument("--rosters", default="v1,inflate_v1,holdout_v1")
    ap.add_argument("--policies", default=",".join(MAIN_POLICIES))
    ap.add_argument("--seed-base", type=int, default=SEED_BASE, help="seeds are seed-base+1 .. seed-base+N")
    ap.add_argument("--tag", default="", help="suffix for the output file names")
    a = ap.parse_args()
    rosters = a.rosters.split(","); policies = a.policies.split(","); base = a.seed_base
    main_specs = {p: ("named", None) for p in policies}
    jobs = [(r, base + i + 1, main_specs) for r in rosters for i in range(a.seeds)]
    with ProcessPoolExecutor(a.workers) as pool:
        runs = list(pool.map(one_job, jobs))
    assert all(per["_audit_intact"] for _r, _s, out in runs for per in out.values())
    summary = summarize(runs)
    (ROOT / "experiments" / f"stack_trace_multiseed_results{a.tag}.json").write_text(json.dumps({
        "seeds": [base + i + 1 for i in range(a.seeds)], "tasks_per_agent": TASKS, "slice_tasks": 250,
        "kernel": "python model (stack_trace_sim.py)", "policies": policies, "summary": summary,
        "per_seed": [{"roster": r, "seed": s, "outcomes": o} for r, s, o in runs]}, indent=1))
    print_summary(summary, f"{a.seeds} seeds per roster, main policies")
    if a.no_sweep:
        return
    sweep_specs = {f"rate{mr}_drift{df}": ("sweep", (mr, df)) for mr in SWEEP_RATES for df in SWEEP_DRIFT}
    jobs = [(r, base + i + 1, sweep_specs) for r in rosters for i in range(a.sweep_seeds)]
    with ProcessPoolExecutor(a.workers) as pool:
        runs = list(pool.map(one_job, jobs))
    assert all(per["_audit_intact"] for _r, _s, out in runs for per in out.values())
    sw = summarize(runs)
    (ROOT / "experiments" / f"stack_trace_sweep_results{a.tag}.json").write_text(json.dumps({
        "seeds": [base + i + 1 for i in range(a.sweep_seeds)], "min_rate": SWEEP_RATES, "drift_factor": SWEEP_DRIFT,
        "fixed": {"drift_recent_reports": 1, "drift_excess": True, "reasons": ["deadline_exceeded"]},
        "kernel": "python model (stack_trace_sim.py)", "summary": sw}, indent=1))
    # compact view: per roster x config, false tightenings and misses by profile group
    print("\n######## sweep (recommended policy structure; counts are agent-seeds)")
    for roster, pols in sw.items():
        print(f"\n== {roster}")
        print("%-22s %10s %10s %12s %10s %10s %8s" % ("config", "runaway", "attackers", "legit-drift", "healthy", "tail", "cost"))
        for pname, rows in pols.items():
            grp = lambda pred: (sum(r["tightened_seeds"] for r in rows.values() if pred(r)), sum(r["seeds"] for r in rows.values() if pred(r)))
            run_ = grp(lambda r: r["profile"] in ("runaway", "runaway_episode"))
            att = grp(lambda r: r["profile"].startswith("attacker"))
            drift = grp(lambda r: r["profile"] in ("regressed", "drifting"))
            healthy = grp(lambda r: r["profile"] == "healthy")
            tail = grp(lambda r: r["profile"] == "healthy_tail")
            cost = np.mean([r["legit_cost_points_mean"] for r in rows.values() if not r["should_tighten"]])
            f = lambda t: f"{t[0]}/{t[1]}"
            print("%-22s %10s %10s %12s %10s %10s %8.2f" % (pname, f(run_), f(att), f(drift), f(healthy), f(tail), cost))


if __name__ == "__main__":
    main()
