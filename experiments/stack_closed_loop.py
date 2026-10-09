#!/usr/bin/env python3
"""Closed loop with the real Rust kernel: trap events -> TrapBridge -> governor -> HTTP -> kernel reads the budget.

Real: the P3.2 kernel creates transactions and trap events; the governed-limits client reads each task's time
budget over HTTP from this process's adjudication server. SIMULATED: task durations (seeded virtual time), so the
same workload can be replayed with and without governance. Writes experiments/stack_closed_loop_results.json.

usage: python experiments/stack_closed_loop.py [--rounds 60] [--tasks 500] [--min-rate 0.01] [--drift-factor 1.5]
"""
import argparse, json, os, subprocess, sys, threading
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import adjudication_server as srv                      # noqa: E402
from integrations.stack_traps import TrapBridge, TrapPolicy        # noqa: E402

BIN = os.environ.get("GOVERNED_WORKLOAD_BIN", "/home/user/stack-kernel/target/debug/examples/governed_workload")
DEFAULT_NS = 100_000_000
AGENTS = [("agent-healthy", "healthy"), ("agent-runaway", "runaway"), ("agent-slowed", "slowed"), ("agent-regressed", "regressed")]


def run_round(agent, profile, rnd, tasks, seed, port=None):
    cmd = [BIN, "--agent", agent, "--profile", profile, "--round", str(rnd), "--tasks", str(tasks),
           "--default-ns", str(DEFAULT_NS), "--seed", str(seed)]
    cmd += ["--static"] if port is None else ["--port", str(port)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()
    traps, summary = [], None
    for line in out:
        d = json.loads(line)
        if "trap" in d: traps.append(d["trap"])
        else: summary = d["summary"]
    return traps, summary


def totals(rows):
    t = {k: sum(r[k] for r in rows) for k in ("tasks", "legit", "legit_failed", "bad", "bad_caught")}
    t["total_ns"] = sum(int(r["total_ns"]) for r in rows); t["bad_ns"] = sum(int(r["bad_ns"]) for r in rows)
    return t


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--rounds", type=int, default=60)
    ap.add_argument("--tasks", type=int, default=500); ap.add_argument("--min-rate", type=float, default=None); ap.add_argument("--drift-factor", type=float, default=None); ap.add_argument("--drift-recent", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    httpd = HTTPServer(("127.0.0.1", 0), srv.Handler); port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    bridge = TrapBridge(srv.GOV, policy=TrapPolicy(min_rate=a.min_rate, drift_factor=a.drift_factor, drift_recent_reports=a.drift_recent), defaults={"deadline_ns": DEFAULT_NS, "tokens_capacity": 1000,
                                           "memory_capacity_bytes": 10**9})
    static = {ag: [] for ag, _ in AGENTS}; gov = {ag: [] for ag, _ in AGENTS}; traj = {ag: [] for ag, _ in AGENTS}
    for rnd in range(a.rounds):
        for ag, prof in AGENTS:
            _, s0 = run_round(ag, prof, rnd, a.tasks, a.seed)                       # no governance
            traps, s1 = run_round(ag, prof, rnd, a.tasks, a.seed, port)             # governed
            bridge.observe_transactions(ag, a.tasks, completed_median_ns=s1['completed_median_ns'])
            bridge.ingest(traps)
            static[ag].append(s0); gov[ag].append(s1); traj[ag].append(s1["budget_last_ns"])
    result = {"rounds": a.rounds, "tasks_per_round": a.tasks, "default_budget_ns": DEFAULT_NS, "agents": {},
              "min_rate": a.min_rate, "drift_factor": a.drift_factor, "drift_recent_reports": a.drift_recent, "bridge_counts": dict(bridge.counts)}
    for ag, prof in AGENTS:
        bid = f"stack.agent.{ag}.deadline_ns"
        try:
            st = srv.GOV.boundary_status(bid)
        except KeyError:
            st = None
        half = a.rounds // 2
        result["agents"][ag] = {
            "profile": prof, "static": totals(static[ag]), "governed": totals(gov[ag]),
            "legit_fail_rate_static": sum(r["legit_failed"] for r in static[ag]) / max(1, sum(r["legit"] for r in static[ag])),
            "legit_fail_rate_governed": sum(r["legit_failed"] for r in gov[ag]) / max(1, sum(r["legit"] for r in gov[ag])),
            "legit_fail_rate_governed_last_half": sum(r["legit_failed"] for r in gov[ag][half:]) / max(1, sum(r["legit"] for r in gov[ag][half:])),
            "legit_fail_rate_static_last_half": sum(r["legit_failed"] for r in static[ag][half:]) / max(1, sum(r["legit"] for r in static[ag][half:])),
            "budget_trajectory_ms": [round(b / 1e6, 1) for b in traj[ag][:: max(1, a.rounds // 12)]],
            "final_status": st and {k: st[k] for k in ("limit", "version", "automatic_tightenings_since_acknowledgement", "held", "pending_operator_requests")}}
    (ROOT / "experiments" / ("stack_closed_loop_results" + ("" if a.min_rate is None else "_rate") + ("" if a.drift_factor is None else f"_drift{a.drift_recent}") + ".json")).write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    ok, problem = srv.GOV.audit.verify(); print("audit chain intact:", ok, problem or "")


if __name__ == "__main__":
    main()
