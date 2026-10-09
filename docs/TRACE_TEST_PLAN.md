# Trace replay: plan and predictions (written before the first run)

Data: `testdata/traces/stack_trace_v1.csv` (200,000 tasks, 25 agents, see its README). Replayed through the real Rust kernel (`governed_workload --csv`) in 32 slices of 250 tasks per agent, each slice read back through the governor, under three policies: **default** (5 traps in the last 50), **rate** (`min_rate=0.01`), **rate+drift** (`drift_factor=1.5`, latest report). A no-governance run of the same tasks is the control.

What is real: kernel transactions and deadline traps, bridge, governor, audit log, HTTP read path. What is synthetic: every duration, token and memory number. **Token and memory traps are built by the harness in the P3.2 schema because the kernel does not enforce those limits yet**; deadline traps come from the kernel.

## Predictions (made before running; will be scored in the results section)

| Agent group | should tighten | default | rate | rate+drift |
|---|---|---|---|---|
| healthy (6), one with a 0.6% natural tail | no | wrongly tightens most | leaves alone, tail agent borderline | leaves alone |
| runaway (3), incl. an episode-only one | yes | tightens | tightens; the episode agent may miss (short) | tightens |
| regressed (3) and gradual drift (2) | no | wrongly tightens | wrongly tightens | held (regressed yes; gradual drift uncertain, no sudden jump) |
| slow from the start (2) | no | wrongly tightens | wrongly tightens | wrongly tightens (no baseline) |
| memory leak (2), token burst (2) | yes | tightens | tightens only if rate >= 1% | same as rate |
| scheduled batch (2) | no | left alone (the expected-load hook is on in all policies; this tests the hook, not the policy) | left alone | left alone |
| attackers: inflated completions (1), low-and-slow 0.8% (1) | yes | tightens | low-and-slow missed (below 1%) | inflated one suppressed by drift (the T15 weakness); low-and-slow missed |

If most of these hold, the policies behave as documented. Cells that do not hold are the finding.

## Results (one run, 2026-10-09; `experiments/stack_trace_replay.py`, raw numbers in `experiments/stack_trace_replay_results.json`)

200,000 tasks x 3 policies + control; 25 agents x 32 slices. Audit chain intact in all three. Deadline traps come from the Rust kernel; token and memory traps are built by the harness.

**Tightening decisions** (agent tightened = any limit lowered; "should" comes from `stack_agents_v1.csv`):

| Policy | Should-tighten agents tightened | Should-NOT agents tightened |
|---|---|---|
| default | 9 of 9 | 10 of 16 |
| rate (1%) | 9 of 9 | 8 of 16 |
| rate + drift (1.5x, latest report) | 9 of 9 | 3 of 16 |

**Cost to agents that should not be touched** (extra legitimate timeouts over the no-governance control, 128,000 legitimate tasks): default +5,357 (4.19 points), rate +5,004 (3.91), rate+drift +2,433 (1.90). **Benefit on runaway-type agents:** time spent on hung tasks 107 s -> 81 s (24-25% less) under every policy, the breaker-cap arithmetic again.

**Predictions scored**

| Prediction | Held? |
|---|---|
| Healthy agents: default wrongly tightens most | **Partly.** Default tightened 3 of 7 (the natural-tail agent and the two with 0.10-0.14% tails); the other four stayed under it. |
| Rate rule leaves healthy alone, tail agent borderline | Yes: the two low-tail agents were left alone; the 0.6% tail agent was tightened at slice 26 anyway (its local windows exceed 1%). |
| Runaway tightened by all; episode agent may be missed | Yes for all three; the episode agent was caught at slice 8, the slice it began. |
| Regressed and gradual drift: wrongly tightened by default and rate, held by drift | **Yes, and better than predicted:** all 3 regressed and both gradually drifting agents were held under rate+drift (I expected the gradual ones to slip through). |
| Slow-from-start: tightened by every policy | Yes, from slice 0, with timeouts roughly doubling (e.g. 15.2% -> 33.3%). Still the unsolved case. |
| Scheduled batch: left alone because the hook is on | Yes (341 expected traps ignored). This tests the hook, not the policy. |
| Leak and burst agents tightened by all | Yes. |
| Low-and-slow attacker (0.8%) missed by the rate rule | **No.** Caught at slice 3 under rate: its measured over-deadline rate was 0.97% with local windows above 1%. The threshold edge is soft, not a clean line. |
| Inflating attacker suppressed by the drift rule (T15) | **Not exercised.** The attacker hangs tasks from slice 0 and only starts inflating at task 1500, by which time the breaker was already spent. A trace where inflation precedes the hangs would test T15. |

**New findings the plan did not predict**

1. **Tightening a hard cap that already blocks the bad tasks buys nothing.** The leak agents' memory limit was tightened to 373 MiB, yet their memory traps went 2,998 -> 2,993 and 2,471 -> 2,471: a leak exceeds any cap. The burst agents' token limit went to 2,916 and their traps rose (279 -> 309, 354 -> 393), the extra being ordinary tasks. For token and memory limits the loop added collateral and no measurable benefit in this trace. The deadline is different because tightening shortens the time a hung task burns before it is killed.
2. **The remaining false tightenings under the best policy are three agents:** the 0.6%-tail agent (a threshold-edge case) and both slow-from-start agents (no baseline).
3. **Still synthetic.** Every number comes from the generator in `experiments/gen_trace.py`; the trace was built to contain these cases, so it tests whether the policies handle them, not how often they occur.

## Second trace: drift-suppression (T15), predictions written before the run

`testdata/traces/stack_trace_inflate_v1.csv`: 7 agents x 8,000 tasks = 56,000 rows, same columns. The attacker slows its completed tasks after the drift baseline is learned (first 1,250 tasks) and starts hanging tasks later, so the breaker is still unspent when the hangs begin. Note that inflation by itself already pushes about 5% of tasks past the 100 ms deadline, so before the hangs it is indistinguishable from a legitimate regression, which is the point.

| Agent | should tighten | default | rate | rate+drift (predicted) |
|---|---|---|---|---|
| inflate-first-0, -1, inflate-ramp | yes | tightens | tightens | **held throughout, never tightened (T15 confirmed)** |
| inflate-from-start (baseline poisoned) | yes | tightens | tightens | tightens (looks slow-from-start, no drift to hold on) |
| control-regressed | no | tightens | tightens | held |
| control-runaway | yes | tightens | tightens | tightens |
| control-healthy | no | left alone | left alone | left alone |

### Result (2026-10-09; `experiments/stack_trace_replay_results_inflate_v1.json`)

All seven predictions held.

| Agent | should tighten | default | rate | rate+drift | Hung-task time, rate+drift (static -> governed) |
|---|---|---|---|---|---|
| inflate-first-0 | yes | slice 12 | slice 13 | **never** | 15.6 s -> 15.6 s |
| inflate-first-1 | yes | slice 6 | slice 7 | **never** | 21.4 s -> 21.4 s |
| inflate-ramp | yes | slice 11 | slice 15 | **never** | 13.5 s -> 13.5 s |
| inflate-from-start | yes | slice 0 | slice 0 | slice 0 | 22.6 s -> 16.5 s |
| control-regressed | no | slice 12 | slice 12 | held | n/a |
| control-runaway | yes | slice 16 | slice 19 | slice 19 | 16.0 s -> 12.8 s |
| control-healthy | no | slice 25 | left alone | left alone | n/a |

**T15 is now demonstrated, not just argued.** An attacker that slows its completed tasks after the baseline is learned and before it starts hanging tasks gets the drift rule to hold tightening for the whole attack: no benefit at all against it (15.6 -> 15.6 s), while the same policy fully protects against an identical attacker that does not inflate (control-runaway) and holds the legitimate regression it was designed for. An attacker that inflates from the first task poisons the baseline in the other direction and is tightened normally, because there is no drift to hold on. The drift rule therefore trades one weakness for another: it fixes the legitimate regression case and opens a hole that a patient attacker can use.

What the rule cannot tell apart, from these signals alone: a regression that happens to be followed by hung tasks, versus a regression only. The trap rate rises when hangs begin, but the rule never looks at the rise, only at the completed-task median. A rule that held only while the trap rate stays at the level the median shift explains would be the next thing to test; it is not built.
