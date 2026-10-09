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

## Rate rule (`TrapPolicy(min_rate=0.01)`, off by default)

Added after the result above: tighten only if traps are at least 1% of the agent's last 2,000 transactions. The kernel must report activity (`TrapBridge.observe_transactions`); without it nothing tightens. Same workload and seed (`experiments/stack_closed_loop_results_rate.json`). The 1% threshold was chosen from what I knew of the workload (0.05% vs 5% vs 15%) before running, not swept, but it is still a number picked for this simulation.

| Agent | Legit timeouts, static -> default policy -> rate rule | Final budget (default -> rate) |
|---|---|---|
| healthy | 0.05% -> 0.28% -> **0.05%** | 72.9 ms -> **100 ms (untouched)** |
| runaway | 0.07% -> 0.42% -> 0.42% (same benefit: 147.5 s -> 108.3 s) | 72.9 -> 72.9 ms |
| slowed | 15.1% -> 34.2% -> 34.2% | 72.9 -> 72.9 ms |

So the rate rule removes the harm to the healthy agent at no cost to the runaway benefit. It does nothing for the slowed agent: 15% timeouts looks exactly like an attack from the trap stream. That case needs a person, or a signal that is not trap counts (for example, a latency shift that the agent's own history explains).

## Drift rule (`TrapPolicy(drift_factor=1.5)`, off by default)

For the slowed agent. The kernel also reports the median duration of the tasks that completed (`observe_transactions(..., completed_median_ns=)`). The bridge freezes a baseline from an agent's first 5 reports. If the agent's recent completed tasks are slower than that baseline by `drift_factor` while traps are high, it is a regression: the bridge does not tighten, writes a `drift_held` entry to the audit log once per episode, and, if the limit was already tightened, files a loosening request back to the configured value for a person. A runaway looks different: its completed tasks stay at baseline while the killed ones push the trap rate up.

New agent `regressed`: healthy for 20 rounds, then every task is 3x slower. With the rate rule on in all rows:

| Variant | regressed: legit timeouts (static 10.2%) | regressed: final budget | restore request waiting |
|---|---|---|---|
| rate rule only | 22.9% | 72.9 ms | no |
| drift, median of last 3 reports | 22.9% | 72.9 ms | **yes (1)** |
| drift, latest report only | **10.2%** | **100 ms (untouched)** | none needed |

- **Why smoothing failed:** one slow round carries about 75 traps, enough to spend the whole breaker cap before a 3-report median moves. The unsmoothed variant works because the report arrives before that round's traps are processed. I tried the unsmoothed variant after seeing the smoothed one fail; the reason is mechanical, but it is a choice made on this result.
- **No effect on the other agents:** healthy stays untouched (rate rule), the runaway still gets its 27% saving (its completed tasks did not slow, so it is not held).
- **The slowed agent is still harmed (34%).** It is slow from the first round, so it has no earlier baseline to drift from. The drift rule needs history.
- **New attack surface:** whoever controls the reported medians can suppress tightening by inflating them (this only removes autonomy, never loosens a limit), and a baseline learned while the agent is already misbehaving is poisoned (the same weakness found in `docs/BASELINE_POISONING.md`). Neither is mitigated here.

## Not tested

The rate rule's threshold and the drift factor on other workloads or other seeds (one seed throughout). Token and memory limits are not enforced by the kernel yet, so only the deadline was exercised. Durations are simulated; real workloads have different tails.
