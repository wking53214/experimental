# Closed loop with the real STACK kernel

`experiments/stack_closed_loop.py` runs the P3.2 kernel (Rust, `stack-p3-2`) under a workload and closes the loop: the kernel emits real `TrapEvent`s, `integrations/stack_traps.py` turns them into tightening signals, the governor lowers the agent's time budget, and the kernel reads the new budget over HTTP before its next task (`GovernedLimits`).

**Real:** kernel transactions and trap events, the bridge, the governor and its audit log, the HTTP read path.
**Simulated:** task durations (seeded virtual time, so the same workload is replayed with and without governance). Default budget 100 ms. 60 rounds x 500 tasks per agent = 30,000 tasks each. One run, one seed; not a distribution.

| Agent | What it does | Legit-task timeout rate, static -> governed | Time spent on runaway tasks | Final budget |
|---|---|---|---|---|
| healthy | median 20 ms, almost always inside budget | 0.05% -> 0.28% (last half 0.05% -> 0.49%) | n/a | 72.9 ms (-27%) |
| runaway | healthy, plus 5% of tasks that run 500 ms | 0.07% -> 0.42% | 147.5 s -> 108.3 s (-27%) | 72.9 ms (-27%) |
| slowed | legitimately slower after a regression, median 60 ms | 15.1% -> 34.2% | n/a | 72.9 ms (-27%) |

Audit chain intact at the end. Raw numbers: `experiments/stack_closed_loop_results.json`.

## What this shows

1. **The plumbing works.** Real trap events become real limit changes that the kernel actually obeys, bounded by the floor, the breaker (3 tightenings, 27%) and the configured ceiling.
2. **The benefit is real but equals the breaker arithmetic.** Against a genuine runaway the loop saves 27% of the time those tasks consume, and no more, because the breaker stops it there.
3. **The default trap policy punishes healthy and merely slow agents.** `TrapPolicy` counts "5 traps among the last 50 trap events", not a rate. A healthy agent with a 0.05% natural timeout rate eventually collects five traps and gets tightened to the breaker cap, which multiplied its legitimate timeouts about 10x. A legitimately slowed agent had its failures more than doubled.
4. **Trap events do not escape the false-alarm problem.** I expected real enforcement events to avoid the detector's false alarms (they are real, not statistical guesses). The first half of that holds: the traps are real. But a real trap is not evidence of an attack, and the policy cannot tell a runaway from a slow-but-legitimate agent. Only a person can, which is the design's answer (the breaker holds, a human loosens) but it means the autonomy buys little here.

## Not tested

A rate-based policy (traps per second or per task) would likely stop the healthy-agent case, since 0.05% and 5% differ 100x. It would not separate the runaway from the slowed agent (5% vs 15% timeouts are both "high"). Not built or measured. Token and memory limits are not enforced by the kernel yet, so only the deadline was exercised. Durations are simulated; real workloads have different tails.
