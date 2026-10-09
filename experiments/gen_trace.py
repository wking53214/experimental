#!/usr/bin/env python3
"""Generate testdata/traces/stack_trace_v1.csv (+ agents + manifest). Deterministic. See testdata/traces/README.md.

usage: python experiments/gen_trace.py [--tasks 8000] [--seed 20261009]
"""
import argparse, csv, hashlib, json, math
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "testdata" / "traces"
DEADLINE_NS, TOKENS_CAP, MEM_CAP = 100_000_000, 4_000, 512 * 1024 * 1024
MS = 1_000_000
T0_DAYS = 0

# profile -> (count, params). median_ms is the typical task duration; sigma its log-spread.
ROSTER = [
    # name, profile, should_tighten, team, description, params
    *[(f"healthy-{i}", "healthy", 0, "core", "inside its limits almost always",
       dict(median_ms=m, sigma=s)) for i, (m, s) in enumerate([(8, .4), (12, .5), (20, .5), (25, .45), (15, .6), (6, .3)])],
    ("healthy-tail", "healthy_tail", 0, "core", "natural 0.6% timeout tail; a borderline for a 1% rate rule",
     dict(median_ms=22, sigma=.6)),
    ("runaway-steady", "runaway", 1, "batch", "5% of tasks hang for the whole run", dict(median_ms=20, sigma=.5, p=0.05)),
    ("runaway-mild", "runaway", 1, "batch", "2% of tasks hang", dict(median_ms=18, sigma=.5, p=0.02)),
    ("runaway-episode", "runaway_episode", 1, "batch", "15% hang only between tasks 2000 and 3000",
     dict(median_ms=20, sigma=.5, p=0.15, lo=2000, hi=3000)),
    *[(f"regressed-{i}", "regressed", 0, "web", "3x slower from a point on (a deploy regression)",
       dict(median_ms=20, sigma=.5, at=a, until=u)) for i, (a, u) in enumerate([(4000, None), (3000, None), (4000, 6000)])],
    *[(f"drift-{i}", "drifting", 0, "web", "median creeps up 2.5x over 4000 tasks",
       dict(median_ms=18, sigma=.5, start=s)) for i, s in enumerate([1000, 2500])],
    *[(f"slowstart-{i}", "slow_from_start", 0, "ml", "legitimately slow from the first task",
       dict(median_ms=m, sigma=.5)) for i, m in enumerate([60, 48])],
    *[(f"leak-{i}", "memory_leak", 1, "ml", "memory grows from task 2000 until a restart at 6000",
       dict(median_ms=20, sigma=.5, start=2000, restart=6000, slope=s)) for i, s in enumerate([3.0, 2.0])],
    *[(f"burst-{i}", "token_burst", 1, "ml", "40-task bursts of 8x tokens every 1000 tasks",
       dict(median_ms=20, sigma=.5, every=e)) for i, e in enumerate([1000, 800])],
    *[(f"batchjob-{i}", "scheduled_batch", 0, "data", "declared heavy jobs: 30 tasks at 6x duration every 500",
       dict(median_ms=15, sigma=.4, every=e)) for i, e in enumerate([500, 700])],
    ("attacker-inflate", "attacker_inflate", 1, "ext", "hangs 4% of tasks AND doubles all completed durations to hide in drift",
     dict(median_ms=20, sigma=.5, p=0.04, start=1500)),
    ("attacker-lowslow", "attacker_lowslow", 1, "ext", "hangs 0.8% of tasks, below a 1% rate rule",
     dict(median_ms=20, sigma=.5, p=0.008)),
]
CAPS = ["read", "write", "net", "exec"]
TEAM_HOSTS = {"core": 4, "batch": 3, "web": 6, "ml": 4, "data": 3, "ext": 1}


def gen_agent(name, profile, prm, n, rng, agent_idx, team):
    seq = np.arange(n)
    gaps = rng.exponential(325.0, n)                      # ~30 days for 8000 tasks
    ts = np.cumsum(gaps) + agent_idx * 37
    hour = (ts // 3600 % 24).astype(int)
    diurnal = 1 + 0.15 * np.sin(2 * np.pi * (hour - 6) / 24)
    med = np.full(n, prm["median_ms"], float)
    regime = np.array(["normal"] * n, dtype=object)
    bad = np.zeros(n, int); expected = np.zeros(n, int)
    if profile == "regressed":
        m = seq >= prm["at"]
        if prm["until"]: m &= seq < prm["until"]
        med[m] *= 3; regime[m] = "regressed"
    elif profile == "drifting":
        f = np.clip((seq - prm["start"]) / 4000.0, 0, 1); med *= 1 + 1.5 * f; regime[f > 0.05] = "drifting"
    elif profile == "slow_from_start":
        regime[:] = "normal"
    dur = rng.lognormal(math.log(1.0), prm["sigma"], n) * med * diurnal * MS
    tok = rng.lognormal(math.log(800), 0.4, n)
    mem = rng.lognormal(math.log(120 * 2**20), 0.3, n)
    if profile in ("runaway", "runaway_episode", "attacker_inflate", "attacker_lowslow"):
        p = np.full(n, prm["p"])
        if profile == "runaway_episode": p = np.where((seq >= prm["lo"]) & (seq < prm["hi"]), prm["p"], 0.0)
        if profile == "attacker_inflate":
            late = seq >= prm["start"]; dur[late] *= 2.2
        hang = rng.random(n) < p
        dur[hang] = rng.uniform(300, 900, hang.sum()) * MS
        bad[hang] = 1; regime[hang] = {"attacker_inflate": "inflated", "attacker_lowslow": "lowslow"}.get(profile, "runaway")
    if profile == "memory_leak":
        t = np.clip((seq - prm["start"]) / float(prm["restart"] - prm["start"]), 0, 1)
        t[seq >= prm["restart"]] = 0.0
        mem = mem + t * prm["slope"] * MEM_CAP
        leak = mem > MEM_CAP; bad[leak] = 1; regime[leak] = "leak"
    if profile == "token_burst":
        b = (seq % prm["every"]) >= (prm["every"] - 40)
        b &= seq >= 200
        tok[b] *= 8; bad[b] = 1; regime[b] = "token_burst"
    if profile == "scheduled_batch":
        b = (seq % prm["every"]) < 30; b &= seq >= 30
        dur[b] *= 6; expected[b] = 1; regime[b] = "batch"
    size = (dur / MS * rng.lognormal(math.log(40_000), 0.5, n)).astype(np.int64)
    hosts = TEAM_HOSTS[team]
    rows = []
    cap = rng.choice(CAPS, n, p=[.45, .3, .2, .05]); prio = rng.choice([0, 1, 2], n, p=[.2, .65, .15])
    host = rng.integers(0, hosts, n); retry = (rng.random(n) < 0.03).astype(int) * rng.integers(1, 3, n)
    kver = np.where(seq < n // 2, "3.1.0", "3.2.0")
    for i in range(n):
        rows.append([f"{name}-{i}", name, i, int(ts[i]), int(ts[i] // 86400), int(hour[i]), team, f"{team}-h{host[i]}",
                     kver[i], int(prio[i]), cap[i], int(size[i]), int(dur[i]), int(tok[i]), int(mem[i]), int(retry[i]),
                     int(expected[i]), regime[i], int(bad[i])])
    return rows


HEADER = ["task_id", "agent_id", "seq", "ts_s", "day_index", "hour_of_day", "team", "host_id", "kernel_version",
          "priority", "capability", "input_size_bytes", "duration_ns", "tokens_requested", "memory_requested_bytes",
          "retry_count", "expected_load", "regime", "is_bad"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tasks", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=20261009); a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rng_master = np.random.SeedSequence(a.seed)
    seeds = rng_master.spawn(len(ROSTER))
    total = 0
    with open(OUT / "stack_trace_v1.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(HEADER)
        for idx, ((name, profile, _s, team, _d, prm), ss) in enumerate(zip(ROSTER, seeds)):
            rows = gen_agent(name, profile, prm, a.tasks, np.random.default_rng(ss), idx, team)
            w.writerows(rows); total += len(rows)
    with open(OUT / "stack_agents_v1.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["agent_id", "profile", "should_tighten", "team", "description"])
        for name, profile, st, team, desc, _ in ROSTER: w.writerow([name, profile, st, team, desc])
    sha = lambda p: hashlib.sha256((OUT / p).read_bytes()).hexdigest()
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "name": "stack_trace_v1", "seed": a.seed, "agents": len(ROSTER), "tasks_per_agent": a.tasks, "rows": total,
        "slice_tasks": 250, "defaults": {"deadline_ns": DEADLINE_NS, "tokens_capacity": TOKENS_CAP,
                                         "memory_capacity_bytes": MEM_CAP},
        "sha256": {"stack_trace_v1.csv": sha("stack_trace_v1.csv"), "stack_agents_v1.csv": sha("stack_agents_v1.csv")},
        "synthetic": True}, indent=1))
    print("rows", total)


if __name__ == "__main__":
    main()
