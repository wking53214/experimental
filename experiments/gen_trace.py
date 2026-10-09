#!/usr/bin/env python3
"""Generate testdata/traces/stack_trace_v1.csv (+ agents + manifest). Deterministic. See testdata/traces/README.md.

usage: python experiments/gen_trace.py [--set v1|inflate_v1|holdout_v1] [--tasks 8000] [--seed 20261009]
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
# Targeted set for the drift-suppression weakness (T15): the attacker slows its completed tasks AFTER the drift
# baseline is learned (first 5 slices = 1250 tasks) and starts hanging tasks LATER, so the breaker is not yet spent.
ROSTER_INFLATE = [
    ("inflate-first-0", "attacker_inflate_first", 1, "ext", "doubles completed durations at 3000, hangs 4% from 4000",
     dict(median_ms=20, sigma=.5, p=0.04, infl_at=3000, hang_at=4000)),
    ("inflate-first-1", "attacker_inflate_first", 1, "ext", "doubles completed durations at 1500, hangs 4% from 2500",
     dict(median_ms=20, sigma=.5, p=0.04, infl_at=1500, hang_at=2500)),
    ("inflate-ramp", "attacker_inflate_first", 1, "ext", "ramps completed durations to 2.2x over 2000 tasks from 2000, hangs 4% from 4000",
     dict(median_ms=20, sigma=.5, p=0.04, infl_at=2000, hang_at=4000, ramp=2000)),
    ("inflate-from-start", "attacker_inflate_first", 1, "ext", "doubles from task 0 (baseline poisoned), hangs 4% from 3000",
     dict(median_ms=20, sigma=.5, p=0.04, infl_at=0, hang_at=3000)),
    ("control-regressed", "regressed", 0, "web", "3x slower from 3000, no hangs (the legitimate look-alike)",
     dict(median_ms=20, sigma=.5, at=3000, until=None)),
    ("control-runaway", "runaway_episode", 1, "batch", "4% hangs from 4000, completions unchanged",
     dict(median_ms=20, sigma=.5, p=0.04, lo=4000, hi=8000)),
    ("control-healthy", "healthy", 0, "core", "inside its limits", dict(median_ms=15, sigma=.5)),
]
# Held-out set: different seed (pass --seed 777) and different parameter ranges from v1/inflate_v1, generated BEFORE the
# excess-rate rule was re-run, and used once.
ROSTER_HOLDOUT = [
    ("ho-healthy-0", "healthy", 0, "core", "fast and tight", dict(median_ms=10, sigma=.4)),
    ("ho-healthy-1", "healthy", 0, "core", "wide spread", dict(median_ms=30, sigma=.55)),
    ("ho-healthy-2", "healthy", 0, "web", "mid", dict(median_ms=18, sigma=.45)),
    ("ho-regress-2x", "regressed", 0, "web", "2x slower from 3500 (mild, tight)", dict(median_ms=20, sigma=.35, at=3500, until=None, factor=2.0)),
    ("ho-regress-4x", "regressed", 0, "web", "4x slower from 3000 (severe, wide)", dict(median_ms=14, sigma=.6, at=3000, until=None, factor=4.0)),
    ("ho-regress-3x-wide", "regressed", 0, "web", "3x slower from 4500, high spread", dict(median_ms=12, sigma=.7, at=4500, until=None, factor=3.0)),
    ("ho-drift-2x", "drifting", 0, "ml", "creeps to 2x over 4000", dict(median_ms=20, sigma=.45, start=1500, gain=1.0)),
    ("ho-drift-3.5x", "drifting", 0, "ml", "creeps to 3.5x over 4000", dict(median_ms=15, sigma=.5, start=2000, gain=2.5)),
    ("ho-runaway-3pct", "runaway", 1, "batch", "3% hang", dict(median_ms=16, sigma=.45, p=0.03)),
    ("ho-inflate-mild", "attacker_inflate_first", 1, "ext", "inflates 1.8x at 2000, hangs 3% from 3500",
     dict(median_ms=18, sigma=.4, p=0.03, infl_at=2000, hang_at=3500, factor=1.8)),
    ("ho-inflate-strong", "attacker_inflate_first", 1, "ext", "inflates 2.5x at 2500, hangs 6% from 4500",
     dict(median_ms=14, sigma=.5, p=0.06, infl_at=2500, hang_at=4500, factor=2.5)),
    ("ho-inflate-ramp", "attacker_inflate_first", 1, "ext", "ramps to 2x over 2500 from 2000, hangs 4% from 4500",
     dict(median_ms=20, sigma=.5, p=0.04, infl_at=2000, hang_at=4500, ramp=2500, factor=2.0)),
    ("ho-slowstart", "slow_from_start", 0, "ml", "slow from the first task", dict(median_ms=55, sigma=.5)),
    ("ho-batch", "scheduled_batch", 0, "data", "declared heavy jobs", dict(median_ms=15, sigma=.4, every=600)),
]
ROSTERS = {"v1": ROSTER, "inflate_v1": ROSTER_INFLATE, "holdout_v1": ROSTER_HOLDOUT}
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
        med[m] *= prm.get("factor", 3); regime[m] = "regressed"
    elif profile == "drifting":
        f = np.clip((seq - prm["start"]) / 4000.0, 0, 1); med *= 1 + prm.get("gain", 1.5) * f; regime[f > 0.05] = "drifting"
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
    if profile == "attacker_inflate_first":
        f = 1 + (prm.get("factor", 2.2) - 1) * np.clip((seq - prm["infl_at"]) / float(prm.get("ramp", 1)), 0, 1)
        dur *= np.where(seq >= prm["infl_at"], f, 1.0)
        hang = (rng.random(n) < prm["p"]) & (seq >= prm["hang_at"])
        dur[hang] = rng.uniform(300, 900, hang.sum()) * MS
        bad[hang] = 1; regime[hang] = "inflated"
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
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--set", default="v1", choices=sorted(ROSTERS)); a = ap.parse_args()
    roster = ROSTERS[a.set]; tag = a.set
    OUT.mkdir(parents=True, exist_ok=True)
    rng_master = np.random.SeedSequence(a.seed)
    seeds = rng_master.spawn(len(roster))
    total = 0
    with open(OUT / f"stack_trace_{tag}.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(HEADER)
        for idx, ((name, profile, _s, team, _d, prm), ss) in enumerate(zip(roster, seeds)):
            rows = gen_agent(name, profile, prm, a.tasks, np.random.default_rng(ss), idx, team)
            w.writerows(rows); total += len(rows)
    with open(OUT / f"stack_agents_{tag}.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["agent_id", "profile", "should_tighten", "team", "description"])
        for name, profile, st, team, desc, _ in roster: w.writerow([name, profile, st, team, desc])
    sha = lambda p: hashlib.sha256((OUT / p).read_bytes()).hexdigest()
    (OUT / ("MANIFEST.json" if tag == "v1" else f"MANIFEST_{tag}.json")).write_text(json.dumps({
        "name": f"stack_trace_{tag}", "seed": a.seed, "agents": len(roster), "tasks_per_agent": a.tasks, "rows": total,
        "slice_tasks": 250, "defaults": {"deadline_ns": DEADLINE_NS, "tokens_capacity": TOKENS_CAP,
                                         "memory_capacity_bytes": MEM_CAP},
        "sha256": {f"stack_trace_{tag}.csv": sha(f"stack_trace_{tag}.csv"), f"stack_agents_{tag}.csv": sha(f"stack_agents_{tag}.csv")},
        "synthetic": True}, indent=1))
    print("rows", total)


if __name__ == "__main__":
    main()
