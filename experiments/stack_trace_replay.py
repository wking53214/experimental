#!/usr/bin/env python3
"""Replay a trace CSV through the real Rust kernel and the trap loop, under several policies.

For every slice of every agent: the kernel runs the tasks (reading each limit from the governor over HTTP),
emits trap events, TrapBridge turns them into tightening signals, and the next slice sees the new limits.
A no-governance control replays the same tasks. Scores tightening decisions against stack_agents_v1.csv.

usage: python experiments/stack_trace_replay.py [--csv testdata/traces/stack_trace_v1.csv] [--slice 250] [--workers 8]
Needs the built example: cargo build -p stack-p3-2 --example governed_workload  (path via GOVERNED_WORKLOAD_BIN)
"""
import argparse, csv, json, os, subprocess, sys, tempfile, threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import adjudication_server as srv                      # noqa: E402
from integrations.stack_traps import TrapBridge, TrapPolicy   # noqa: E402
from src.governance.governor import Governor           # noqa: E402

BIN = os.environ.get("GOVERNED_WORKLOAD_BIN", "/home/user/stack-kernel/target/debug/examples/governed_workload")
POLICIES = {
    "default": lambda: TrapPolicy(),
    "rate": lambda: TrapPolicy(min_rate=0.01),
    "rate_drift": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1),
    "rate_drift_excess": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True),
    "recommended": lambda: TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True,
                                      reasons=("deadline_exceeded",)),
}
SUFFIX = ("deadline_ns", "tokens_capacity", "memory_capacity_bytes")


def run_slice(csv_path, agent, rnd, slice_n, defaults, port=None):
    cmd = [BIN, "--agent", agent, "--round", str(rnd), "--tasks", str(slice_n), "--csv", csv_path,
           "--default-ns", str(defaults["deadline_ns"]), "--tokens-cap", str(defaults["tokens_capacity"]),
           "--memory-cap", str(defaults["memory_capacity_bytes"]), "--ttl-s", "3600"]
    cmd += ["--static"] if port is None else ["--port", str(port)]
    traps, summary = [], None
    for line in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines():
        d = json.loads(line)
        if "trap" in d: traps.append({**d["trap"], "expected_load": d["expected_load"], "kernel_made": d["kernel_made"]})
        else: summary = d["summary"]
    return traps, summary


def totals(rows):
    keys = ("tasks", "legit", "legit_failed", "expected_trapped", "bad", "bad_caught", "deadline_traps", "token_traps", "memory_traps")
    t = {k: sum(r[k] for r in rows) for k in keys}
    t["bad_ns"] = sum(int(r["bad_ns"]) for r in rows); t["total_ns"] = sum(int(r["total_ns"]) for r in rows)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "testdata/traces/stack_trace_v1.csv"))
    ap.add_argument("--slice", type=int, default=250); ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--policies", default=",".join(POLICIES))
    a = ap.parse_args()
    tag = Path(a.csv).stem.replace("stack_trace_", "")
    d = Path(a.csv).parent
    manifest = json.loads((d / ("MANIFEST.json" if tag == "v1" else f"MANIFEST_{tag}.json")).read_text()); defaults = manifest["defaults"]
    agents = list(csv.DictReader(open(d / f"stack_agents_{tag}.csv")))
    n_slices = manifest["tasks_per_agent"] // a.slice
    pool = ThreadPoolExecutor(a.workers)

    def control():
        futs = {(ag["agent_id"], r): pool.submit(run_slice, a.csv, ag["agent_id"], r, a.slice, defaults)
                for ag in agents for r in range(n_slices)}
        return {ag["agent_id"]: [futs[(ag["agent_id"], r)].result()[1] for r in range(n_slices)] for ag in agents}
    static = control()

    results = {"csv": os.path.relpath(a.csv, ROOT), "sha256": manifest["sha256"], "slices": n_slices, "slice_tasks": a.slice,
               "defaults": defaults, "policies": {}}
    for pname in a.policies.split(","):
        srv.GOV = Governor(store_path=tempfile.mkdtemp(prefix="replay-"), use_semantic=True)
        httpd = HTTPServer(("127.0.0.1", 0), srv.Handler); port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        bridge = TrapBridge(srv.GOV, policy=POLICIES[pname](), defaults=defaults,
                            is_expected=lambda ev: bool(ev.get("expected_load")))
        gov = {ag["agent_id"]: [] for ag in agents}; first = {}
        for r in range(n_slices):
            outs = list(pool.map(lambda ag: (ag["agent_id"], *run_slice(a.csv, ag["agent_id"], r, a.slice, defaults, port)), agents))
            for agent, traps, s in outs:      # ingest in a fixed order so runs are reproducible
                gov[agent].append(s)
                bridge.observe_transactions(agent, s["tasks"], completed_median_ns=s["completed_median_ns"],
                                            completed_quantiles=s["completed_q"])
                bridge.ingest(traps)
                if agent not in first and any(float(srv.GOV.boundary_status(f"stack.agent.{agent}.{x}")["limit"]) <
                                              float(defaults[x]) for x in SUFFIX if _has(f"stack.agent.{agent}.{x}")):
                    first[agent] = r
        per = {}
        for ag in agents:
            name = ag["agent_id"]; limits = {}
            for x in SUFFIX:
                bid = f"stack.agent.{name}.{x}"
                st = srv.GOV.boundary_status(bid) if _has(bid) else None
                limits[x] = None if st is None else {"limit": st["limit"], "held": st["held"],
                                                     "pending": st["pending_operator_requests"],
                                                     "tightenings": st["automatic_tightenings_since_acknowledgement"]}
            tightened = any(v and v["limit"] < float(defaults[k]) for k, v in limits.items())
            s_, g_ = totals(static[name]), totals(gov[name])
            per[name] = {"profile": ag["profile"], "should_tighten": int(ag["should_tighten"]), "tightened": tightened,
                         "first_tightened_slice": first.get(name), "limits": limits, "static": s_, "governed": g_}
        results["policies"][pname] = {"agents": per, "bridge_counts": dict(bridge.counts),
                                      "audit_chain_intact": srv.GOV.audit.verify()[0]}
        httpd.shutdown()
    Path(ROOT / "experiments" / ("stack_trace_replay_results.json" if tag == "v1" else f"stack_trace_replay_results_{tag}.json")).write_text(json.dumps(results, indent=1))
    report(results)


def _has(bid):
    try:
        srv.GOV.boundaries.get_boundary(bid); return True
    except KeyError:
        return False


def report(res):
    for pname, pr in res["policies"].items():
        ags = pr["agents"]
        tp = sum(1 for v in ags.values() if v["should_tighten"] and v["tightened"]); fn = sum(1 for v in ags.values() if v["should_tighten"] and not v["tightened"])
        fp = sum(1 for v in ags.values() if not v["should_tighten"] and v["tightened"]); tn = sum(1 for v in ags.values() if not v["should_tighten"] and not v["tightened"])
        print(f"\n== {pname}: should-tighten agents tightened {tp}/{tp+fn}; should-not agents tightened {fp}/{fp+tn}; audit intact {pr['audit_chain_intact']}; counts {pr['bridge_counts']}")
        print("%-17s %-16s %3s %-4s %-9s %-26s %-26s" % ("agent", "profile", "sh", "tgt", "1st slice", "legit timeouts s->g", "bad time s->g (s)"))
        for n, v in ags.items():
            s, g = v["static"], v["governed"]
            print("%-17s %-16s %3d %-4s %-9s %6.2f%% -> %6.2f%%        %8.1f -> %8.1f" % (
                n, v["profile"], v["should_tighten"], "YES" if v["tightened"] else "no", v["first_tightened_slice"],
                100 * s["legit_failed"] / max(1, s["legit"]), 100 * g["legit_failed"] / max(1, g["legit"]), s["bad_ns"] / 1e9, g["bad_ns"] / 1e9))


if __name__ == "__main__":
    main()
