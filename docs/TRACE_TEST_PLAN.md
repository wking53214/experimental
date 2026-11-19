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

## Third round: the "excess-rate" hold (design and predictions fixed before any code ran)

**Treating the trace as the real workload.** There is no production data, so these two traces are the workload for every decision from here on. Because both are used for judging, parameters below are fixed from first principles now and not tuned; any later change will be recorded as a change made after seeing results.

**Mechanism.** The kernel also reports quantiles (p50, p75, p90, p95, p99) of each slice's completed task durations. The bridge averages the first five slices' vectors into a baseline distribution. When drift is detected (latest median > 1.5x baseline median, factor f = latest / baseline), the bridge asks: if this agent's old distribution were scaled by f, what share of tasks would exceed the current deadline? (`explained = S0(deadline / f)`, survival function from the baseline quantiles, exponential tail beyond p99.) It compares that with the observed deadline-trap share over the last 4 slices. The hold is **released** (tightening allowed) only if `observed > explained + max(0.02, 0.5 x explained)`. Otherwise the drift hold stands.

**Predictions.**

| Agent | rate+drift now | with the excess rule (predicted) |
|---|---|---|
| inflate-first-0, -1, inflate-ramp | never tightened | released once the hangs begin; tightened late, partial benefit |
| control-regressed, regressed-0/1/2 | held | still held (a 3x slowdown already explains its trap rate) |
| drift-0, drift-1 (gradual) | held | still held; the closest call because the window lags a ramp |
| control-runaway, runaway-* | tightened | unchanged |
| slow from the start, healthy-tail | tightened (false) | unchanged (the rule does not touch them) |

The rule is judged a success if it releases at least 2 of the 3 inflate-first attackers **and** releases none of the 7 legitimate drift/regression agents (5 in v1, 1 in the second trace's control, counting regressed-0/1/2, drift-0/1, control-regressed). A single false release counts as a failure of the "none" half.

### Round 3, first result: the pre-registered check FAILED (2026-10-09)

| Success criterion | Result |
|---|---|
| Releases at least 2 of 3 inflate-first attackers | **Met**: all 3 released (tightened at slices 17, 9, 18); hung-task time 89 s -> 69 s against 89 s -> 66 s for the plain rate rule |
| Releases none of the legitimate drift/regression agents | **Not met**: all 6 (regressed-0/1/2, drift-0/1, control-regressed) were released and tightened late (slices 17-30). Tightening decisions on the 16 should-not agents in the first trace went from 3 wrong (rate+drift) to 8 wrong |

**Diagnosis.** The slowdown factor was estimated as latest completed median / baseline median. Completed tasks exclude the ones the deadline killed, so that median is biased low and the bridge under-explains the trap share. Simulated check: at a true 3x slowdown the bridge explains 11.2% of tasks while the true trapped share is 15.2%, and the threshold (16.8%) sits barely above the truth, so ordinary sampling noise (250 tasks per slice) releases legitimate regressions. This is a modeling error in my design, not a threshold that needs loosening.

**Change made after seeing the result (so no longer a clean test on these two traces):** estimate the slowdown by solving for the f at which the baseline distribution, scaled by f and truncated at the current deadline, has the observed completed median. Parameters (1.5x, 4 slices, 2 points + 50%) are unchanged. To keep the evaluation honest, a third trace with a different seed and different parameter ranges (`holdout_v1`) is generated **before** re-running and is run once.

**Predictions for the rerun:** legitimate agents stay held (0 false releases on the two original traces and the held-out one); inflate-first attackers are still released (at least 2 of 3 on the second trace and the held-out equivalents).

**Held-out trace** (`testdata/traces/stack_trace_holdout_v1.csv`, 14 agents, 112,000 tasks, seed 777, different medians, spreads and slowdown factors: legitimate regressions at 2x, 4x and 3x with wide spread, gradual drifts to 2x and 3.5x, attackers inflating 1.8x, 2.5x and a 2x ramp). Predictions recorded before any run with the corrected estimator:

- Legitimate drift/regression agents (5 in the held-out set: 2x, 4x, 3x-wide, drift-2x, drift-3.5x): **at most 1 false release**. The 4x regression sits at the edge of what the estimator can identify (about 4x), so it is the one most likely to be wrongly released.
- Inflate-first attackers (3 in the held-out set): **at least 2 released**.
- On the two original traces: the corrected rule keeps all 6 legitimate agents held and still releases at least 2 of the 3 attackers.

### Round 3, result with the corrected estimator (2026-10-09; all three traces, five policies)

| | Legitimate drift/regression agents falsely released (tightened) | Inflate-first attackers released | Hung-task time saved |
|---|---|---|---|
| rate+drift (hold only) | 0 of 11 | 0 of 6 | 8-10% |
| **rate+drift+excess (corrected)** | **1 of 11** | **6 of 6** | **21-22%** |
| rate only (no drift hold) | 11 of 11 | 6 of 6 | 25-26% |

By trace: first trace 1 of 5 legitimate released (`drift-0`, the gradual drift to 2.5x, at slice 21), second trace 0 of 1 legitimate and 3 of 3 attackers released, **held-out trace 0 of 5 legitimate and 3 of 3 attackers released** (including the 4x regression at the edge of the estimator's range, and the 2x and 3.5x gradual drifts). Cost to agents that should not be touched: 1.95 points on the first trace against 1.90 for hold-only, 0.00 on the second, 1.91 on the held-out trace (the false tightenings that remain there are the slow-from-start and the natural-tail agents, which this rule does not touch).

**Scored against what I wrote down.**

| Criterion / prediction | Outcome |
|---|---|
| First run: no legitimate agent released | **Failed** (6 of 6 released), diagnosed as truncation bias in the slowdown estimate |
| After the correction, held-out: at most 1 of 5 legitimate released | **Met** (0 of 5) |
| After the correction, held-out: at least 2 of 3 attackers released | **Met** (3 of 3) |
| After the correction, original traces: all 6 legitimate held | **Not met** (1 of 6 released: `drift-0`) |
| After the correction, original traces: at least 2 of the 3 attackers released | **Met** (3 of 3) |

**What this means.** The excess-rate rule closes most of the T15 hole: against attackers who slow their completions first, tightening is delayed by about 3-6 slices instead of never happening, and 21-22% of hung time is saved against 8-10% with the plain hold. It does this at the price of occasional false releases on legitimate gradual drift (1 of 11 here). It is not a clean win: the one failure is in the data the design was developed on, and the held-out result is a single run.

**Limits of the estimator (not all measured on a trace).** The slowdown factor is fitted from the five completed-duration quantiles and is accurate to about 4x. A simulation (not a trace) shows it saturating beyond that: at a true 6x slowdown it explains only 33% of tasks when 64% are killed, so a severe legitimate regression would be wrongly released. It also assumes the old distribution simply scales; a regression that changes the shape (for example a new slow code path hit by 20% of tasks) is not modeled. Both are open.

## Fourth round: many seeds and a threshold sweep, in a Python model of the kernel (predictions written before the run)

**Why.** Every closed-loop number so far is one seed per trace, and the two thresholds (1% rate, 1.5x drift) were fixed from first principles for this workload. Before anyone trusts the recommended policy, the one-seed results need to become distributions, and the thresholds need to be shown not to sit on a knife edge.

**Tool.** `experiments/stack_trace_sim.py` replaces the Rust kernel with a Python model of its limit checks (deadline first, then tokens, then memory; nearest-rank quantiles of completed durations) and keeps everything else (TrapBridge, TrapPolicy, Governor, audit log) as the same code. Checked against the kernel's committed results (`tests/test_trace_sim.py`): identical static totals and identical tightening decisions (which agents, which slice, which limits) on all three committed traces, all five policies. Two things the model found while being checked:

1. The excess-rate release timing depends on how the five quantiles are computed. Linear interpolation moved two attackers' release by 1-2 slices; nearest-rank reproduces the kernel exactly. The rule is sensitive at that level.
2. In the committed v1 run the kernel example did **not** act on a tightened memory cap (the leak agents' memory traps stayed at their static count after the cap came down to 373 MiB; a lower cap should trap more). The model applies the governed cap: 2,998 -> 3,034 memory traps and 7 -> 48-55 extra legitimate memory refusals. Finding 1 of the first round ("tightening a hard cap buys nothing") stands either way, and the recommended policy does not tighten memory. The kernel-side enforcement of token and memory caps is already listed as not done.

A model is not the kernel. It is used here only for what the kernel was already shown to agree with it on.

**Design.** The same three rosters (`gen_trace.py`: v1 with 25 agents, inflate_v1 with 7, holdout_v1 with 14) regenerated under 20 fresh seeds each (1001-1020; the committed traces used 20261009 and 777). Policies: default, rate, rate+drift, recommended. Then a sweep of the recommended policy's two thresholds on the first 10 seeds: `min_rate` in {0.5%, 1%, 2%} x `drift_factor` in {1.3, 1.5, 2.0}, everything else fixed. Counts below are agent-seeds (one agent under one seed).

**Predictions for the recommended policy, 20 seeds.**

| Group | Prediction |
|---|---|
| Runaway agents (steady 5%, mild 2%, episode 15%, control 4%, held-out 3%) | tightened in at least 95% of agent-seeds |
| Low-and-slow attacker (0.8%, below the 1% rule) | caught in between 30% and 80% of seeds: the threshold edge is soft, not a line |
| Inflate-first attackers (6 across the rosters) | released and tightened in at least 80% of agent-seeds under recommended; in at most 10% under rate+drift (the T15 hole) |
| Legitimate 2x-3x regressions (regressed-0/1/2, control-regressed, ho-regress-2x, ho-regress-3x-wide) | falsely tightened in at most 10% of agent-seeds |
| The 4x regression (ho-regress-4x, at the estimator's edge) | falsely tightened in up to 30% of seeds |
| Gradual drifts (drift-0, drift-1, ho-drift-2x, ho-drift-3.5x) | the weak spot: falsely tightened in 10% to 40% of agent-seeds, the 3.5x one most often |
| Healthy agents (10 across the rosters, tails at or below 0.14%) | never tightened (0 agent-seeds) |
| Natural 0.6% tail (healthy-tail) | tightened in 30% to 70% of seeds |
| Slow from the start (3 agents) | tightened in 100% of agent-seeds (the accepted limit) |
| Scheduled batch (3 agents) | never (the expected-load hook) |
| Leak and burst (4 agents) | never under recommended (deadline only); always under default and rate |
| Hung-task time saved on runaway-type agents | between 15% and 27% in every seed; never above 27% (the breaker arithmetic) |

**Predictions for the sweep.**

- `min_rate` 0.5%: catches the low-and-slow attacker in most seeds but also tightens healthy-tail in nearly all seeds and starts tightening some healthy agents. 2%: misses the low-and-slow attacker in most seeds and the 2% runaway (runaway-mild) in about half of them.
- `drift_factor` 1.3: fewer false tightenings on gradual drift (the hold engages earlier), attackers released a slice or two later. 2.0: the 2x regression and the 2x gradual drift never reach the factor, so they are no longer held and are falsely tightened in more than half their seeds.
- No configuration beats (1%, 1.5) on both false tightenings of legitimate agents and attacker detection at once; a configuration that does would be the finding.

**Pass bar, fixed now.** The recommended policy is called robust on this workload if all four hold: runaway agents tightened in at least 95% of agent-seeds; healthy (non-tail) agents tightened in at most 2% of agent-seeds; legitimate regression and drift agents (all 11) falsely tightened in at most 15% of agent-seeds overall; inflate-first attackers tightened in at least 80% of agent-seeds. Any miss is the finding, and the thresholds are not retuned on these seeds.

### Round 4, result (2026-10-14; `experiments/stack_trace_multiseed.py`, 20 seeds x 3 rosters x 4 policies, then the 3 x 3 sweep on 10 seeds; raw numbers in `experiments/stack_trace_multiseed_results.json` and `experiments/stack_trace_sweep_results.json`, both produced by the bridge **before** the recovery fix described below; the fix's own runs are in the `..._recovery_fix*.json` files)

(Results table and scored predictions follow below, after the fix check; the paragraph here records the one change made after seeing the results, and its pre-registered check, before that check was run.)

**Recommended policy, 20 seeds, counts are agent-seeds tightened** (the three rosters pooled; per-agent numbers in the JSON):

| Group | Prediction | Result | Held? |
|---|---|---|---|
| Runaway agents (5) | at least 95% | **100 of 100**; hung-task time saved 15% to 26% (episode agent lowest), never above 27% | Yes |
| Low-and-slow attacker (0.8%) | 30% to 80% of seeds | **19 of 20**, but saving only 0% to 20% (median 14%): caught late and sometimes not at all in time to matter | No, the edge is softer than predicted, in the attacker's disfavour |
| Inflate-first attackers (6) | at least 80% under recommended; at most 10% under rate+drift | **120 of 120** under recommended; **0 of 120** under rate+drift (the T15 hole holds on every seed). Saving under recommended 6% to 24% (medians 13% to 21%) against 19% to 27% under the plain rate rule: the hold costs 3 to 7 slices on every one | Yes |
| Legitimate 2x-3x regressions (6) | at most 10% | **26 of 120 (22%)**: regressed-2 11 of 20 (the recovery defect below), regressed-1 6, ho-regress-3x-wide 6, control-regressed 3, regressed-0 0, ho-regress-2x 0 | No |
| 4x regression | up to 30% | 4 of 20 (20%) | Yes |
| Gradual drifts (4) | 10% to 40%, the 3.5x most often | 9 of 80 (11%): drift-0 (2.5x) 5, drift-1 2, ho-drift-3.5x 2, ho-drift-2x 0 | Yes on the share, no on which agent |
| Healthy agents (10) | never | **20 of 200**: nine agents 0 of 180, `ho-healthy-1` 20 of 20, from slice 1 | No |
| Natural 0.6% tail | 30% to 70% | 8 of 20 (40%) | Yes |
| Slow from the start (3) | 100% | 60 of 60 | Yes |
| Scheduled batch (3) | never | 0 of 60 | Yes |
| Leak and burst (4) | never under recommended; always under default and rate | 0 of 80; 80 of 80 | Yes |

**Pass bar, scored.** Runaway at least 95%: **met**. Healthy at most 2%: **not met** (10%). Legitimate regression and drift at most 15%: **not met** (39 of 220, 18%). Inflate-first at least 80%: **met**. Two of four, so the recommended policy is not robust on this workload by the bar I set. The two failures:

1. `ho-healthy-1` is labelled healthy in the roster (median 30 ms, log-spread 0.55) but its natural over-deadline share is 1.5% to 1.8% on every seed: above the 1% rule by construction. The rate rule cannot tell a legitimately wide-tailed agent from a mild runaway from trap counts alone, and the drift rule does not help because nothing drifts. This is the `slowed` agent of the first closed-loop run in another costume, and it says the 1% threshold is a workload parameter, not a constant. Its cost: legitimate timeouts from 1.5% to 5.0% (3.4 points). The roster label is left as it is; the bar was set against the labels.
2. The regression group fails mostly through one mechanism, found here and fixed below: a regression that **recovers** was tightened right after recovering (regressed-2, 11 of 20 seeds, always 2 slices after its recovery). The rest (regressed-1 at 6 of 20, ho-regress-3x-wide at 6, control-regressed at 3, ho-regress-4x at 4, the gradual drifts at 9 of 80) are the excess rule's false releases, 1 of 11 agents in the single-seed runs and 28 of 200 agent-seeds here (14%), always late (slices 16 to 28) and at small cost (at most 2.9 points on one seed, under 0.5 on average).

**Sweep, 10 seeds, recommended structure with `min_rate` x `drift_factor`.**

| Prediction | Result | Held? |
|---|---|---|
| 0.5%: catches low-and-slow in most seeds, tightens healthy-tail in nearly all, starts on some healthy agents | low-and-slow 10 of 10 (from 9); healthy-tail 10 of 10 (from 4); healthy agents with tails at or below 0.11%: **0 of 60**, untouched at 0.5% too | Two of three; the healthy agents have more margin than I assumed |
| 2%: misses low-and-slow in most seeds and runaway-mild (2%) in about half | low-and-slow **0 of 10**; runaway-mild **10 of 10** (its local windows run above 2%: hangs plus a natural tail) | Half |
| 1.3: fewer false tightenings on gradual drift, attackers a slice or two later | **no change at all** against 1.5, on any agent, in any roster (the regressions and drifts here jump well past both factors; the hold engages at the same slice) | No |
| 2.0: the 2x agents are no longer held and are tightened in more than half their seeds | ho-drift-2x 10 of 10 (from 0); drift-0 9 of 10 (from 1), drift-1 6 of 10 (from 1); ho-regress-2x stays 0 (its rate is under 1% anyway, 0.3%) | Yes |
| No configuration beats (1%, 1.5) on both legitimate false tightenings and attacker detection | None does. 2% is better on cost (`ho-healthy-1` 7 of 10 instead of 10, healthy-tail 0) and loses the low-and-slow attacker; 0.5% gains nothing on detection and tightens the tail agent every time | Yes |

What the sweep says: the drift factor is flat between 1.3 and 1.5 and breaks at 2.0 for 2x regressions, so 1.5 is not on an edge; the rate threshold is on an edge in both directions, because the workload contains agents at 0.6%, 0.8%, 1.5% and 2% that the labels put on different sides of it. No trap-count rule separates them; that needs either an operator setting the threshold per agent class or a signal that is not a count.

**Change made after seeing the results.** `regressed-2` (3x slower from task 4,000, recovered at 6,000) was tightened in 11 of 20 seeds under both rate+drift and recommended, always at slice 26-27, two slices after its recovery. In the committed single-seed run it was not. Mechanism: the drift hold kept the slow period's traps in the trap window and the rate stamps; when completions recovered the hold lifted, and the first natural trap afterwards met "5 traps in the window and at least 1% of the last 2,000 tasks" on the slow period's evidence alone. The traps a hold explained are now spent when the hold lifts through recovery (`drift_recovered` in the bridge counts): the window and stamps are cleared and a fresh pattern at a fresh rate is needed. An agent released by the excess rule keeps its evidence, because there the traps were not explained. Unit tests: `tests/test_integration_stack_traps.py` (`TestDriftRule`).

**Pre-registered check for the fix, written before running it.** Fresh seeds 2001-2010, all three rosters, policies rate+drift and recommended. Predictions: `regressed-2` falsely tightened in at most 1 of 10 seeds (from 11 of 20); every other agent's tightened share moves by at most 10 points against seeds 1001-1020; the inflate-first attackers are still tightened in 100% of agent-seeds under recommended, with their first-tightened slice medians within 1 of before; the runaway agents' hung-time saving is unchanged. If the fix costs attacker detection, that is the finding and the fix is reconsidered.

### Round 4, fix check (2026-10-14; `experiments/stack_trace_multiseed_results_recovery_fix.json` for seeds 2001-2010, `..._recovery_fix_insample.json` for seeds 1001-1020 rerun with the fix)

| Pre-registered prediction | Result | Held? |
|---|---|---|
| `regressed-2` falsely tightened in at most 1 of 10 fresh seeds | rate+drift: **0 of 10** (from 11 of 20). Recommended: **2 of 10**, at slices 19 and 22, which is **before** its recovery at slice 24: these are the excess rule's ordinary false releases during the slow period, not the recovery defect | No as written; the recovery defect itself is gone |
| Every other agent moves by at most 10 points | Two moved by 20 points on 10 seeds, in opposite directions (`regressed-0` 0 of 20 to 2 of 10, `regressed-1` 6 of 20 to 1 of 10), both excess releases at slices 18 to 25. All other 44 agents within 10 points | No as written; see the same-seed diff |
| Inflate-first attackers still 100%, first-slice medians within 1 | 60 of 60. Medians 18 / 11 / 17.5 / 16 / 19 / 19 against 19 / 11.5 / 19 / 16 / 19 / 20.5: two of six moved by 1.5 | Mostly; two medians moved 1.5 slices, earlier not later |
| Runaway saving unchanged | 26 / 25 / 17 / 20 / 26% against the same before | Yes |

**Same-seed diff (the cleaner test).** Rerunning seeds 1001-1020 with the fix changed the decision on exactly 11 agent-seeds under each policy: the 11 `regressed-2` recovery tightenings (slices 24 to 30), all now untightened, cost change 0.00-0.01 points. No other agent-seed changed under either policy. So the fix removes the recovery defect and touches nothing else; the fresh-seed moves on `regressed-0` and `regressed-1` are the excess rule's seed-to-seed noise at 1 to 2 seeds per agent per 10, which the 10-point bar was too tight for.

**Where this leaves the recommended policy.** On this workload, over 20 seeds and after the fix: runaway agents and every inflate-first attacker are tightened on every seed; the price is the hold's 3 to 7 slice delay on attackers who inflate first (hung time saved 6% to 24% instead of 19% to 27%), one legitimately wide-tailed agent above the 1% rule tightened every time, the natural-tail agent 40% of the time, the slow-from-start agents always, and the excess rule falsely releasing a legitimate regression or drift in about 1 of 8 agent-seeds, late and cheaply. The thresholds: 1.5x is flat down to 1.3 and breaks at 2.0; 1% is a workload parameter with agents on both sides of it, and no trap-count rule moves them apart.

## Fifth round: a validation layer for deadline tightenings (design and predictions written before the code ran)

**What question C asks.** The research proposal's critique says the architecture skips validation: nothing establishes that a tightening improved anything rather than just moving violations around. Rounds 1 to 4 judged tightenings against the trace's ground-truth labels, which a running system does not have. This round gives the loop an independent signal it could really have, and judges every tightening with it.

**The signal: a canary.** While an agent's deadline is tightened, the kernel keeps a fixed share of that agent's tasks (every tenth task, 10%) on the original deadline. That is an A/B test the proposal itself suggests. It costs 10% of the saving on hung tasks. Two things are then observable per slice without any label or baseline: the trap share of the main tasks at the tightened deadline D1, and the trap share of the canary tasks at the original deadline D0.

**The verdict.** Four slices after a tightening (and again every four slices while the limit stays tightened), with at least 50 canary tasks seen:
- `delta = max(0, main_share - canary_share)`: tasks the tightening newly kills, that would have completed under D0. These are the collateral, whoever the agent is.
- `benefit_max = canary_share x (D0 - D1)`: time saved per task if every trap at the original deadline were a hang (an upper bound; the hung share is never known).
- `cost = delta x D1`: time burned by the newly killed tasks before they were killed.
- **DEGRADED** if `cost > benefit_max`: even if every original-deadline trap were a hang, the tightening burns more time than it saves. **IMPROVED** if `cost < 0.5 x benefit_max`. **UNCHANGED** between. **UNKNOWN** with too few canary tasks. A tightening whose canary and main shares are both zero is UNCHANGED (it is not doing anything).
- DEGRADED files a restore request to the configured limit for a person (never a loosening) and stops further automatic tightening of that boundary until the person decides. The verdict, the numbers and the request go to the audit log and the governor's validation store.

No distribution is assumed and no baseline is used, so the baseline-poisoning and truncation problems of the drift rule do not apply. What the verdict cannot do is tell whose time it is: an attacker's inflated-but-completing tasks count as collateral like anyone's. That is deliberate, and a limitation to record, not hide.

**Predictions** (20 fresh seeds 3001-3020, three rosters, recommended policy with validation on; counts are agent-seeds):

| # | Prediction |
|---|---|
| P1 | Sustained runaways (steady, mild, ho-runaway-3pct, control-runaway): first verdict IMPROVED in at least 90%; DEGRADED while still hanging tasks in at most 10% |
| P2 | runaway-episode (hangs only between tasks 2,000 and 3,000): IMPROVED during the episode, then DEGRADED with a restore request after it ends, in at least 70% of seeds: the layer notices a tightening that has stopped buying anything |
| P3 | Agents that should not be tightened but are (healthy-tail, ho-healthy-1, the three slow-from-start agents, falsely released regressions and drifts): DEGRADED with a restore request within 6 slices in at least 80% of those agent-seeds; the slow-from-start agents and ho-healthy-1 in 100% |
| P4 | Attackers that inflate their completed durations (attacker-inflate and the six inflate-first agents): DEGRADED in at least 50%. In time terms the tightening burns more on their inflated tasks than it saves on their hangs; the verdict is right about the time and blind to the intent |
| P5 | The low-and-slow attacker: not DEGRADED (UNCHANGED or IMPROVED) in at least 70% |
| P6 | Hung-task time saved on sustained runaways drops by about a tenth (the canary's share) and stays at least 15% |
| P7 | Zero restore requests and zero tightenings on the nine healthy agents with tails at or below 0.2% |
| P8 | Criterion 10: (a), (b), (c), (e) pass; (d) passes on both halves. If (d) fails on the runaway half it will be through noise on the 2% runaway, whose canary sees about 1 hang per slice |

### Round 5, first result (2026-10-14; seeds 3001-3020; `experiments/stack_trace_multiseed_results_validated.json`, policies `recommended` and `validated`)

| # | Prediction | Result | Held? |
|---|---|---|---|
| P1 | Sustained runaways: first verdict IMPROVED in at least 90%; DEGRADED while hanging at most 10% | first IMPROVED **43 of 80**; DEGRADED while still hanging **70 of 80** | **No** |
| P2 | Episode runaway: IMPROVED then DEGRADED after the episode, at least 70% | 13 of 20 | No (65%) |
| P3 | Wrong tightenings: DEGRADED with a restore request within 6 slices in at least 80%; slow-from-start and ho-healthy-1 in 100% | within 6 slices **77 of 121** (64%); ever 100 of 121. Slow-from-start 60 of 60 within 6. ho-healthy-1: 20 of 20 ever, **3 of 20 within 6** | No |
| P4 | Inflating attackers DEGRADED in at least 50% | 147 of 160 ever, 115 of 160 at the first verdict | Yes |
| P5 | Low-and-slow attacker not DEGRADED in at least 70% | not DEGRADED **2 of 20** | **No** |
| P6 | Saving on sustained runaways drops about a tenth and stays at least 15% | 26 to 23%, 24 to 21%, 26 to 23%, 20 to 18% (relative drop 9 to 13%) | Yes |
| P7 | Zero tightenings or restore requests on the nine quiet healthy agents | 0 of 178 | Yes |
| P8 | Criterion 10 passes | **FAIL** on (d): wrong tightenings caught within 6 slices 77 of 121 (need 80%); runaways DEGRADED while hanging 74 of 100 (need at most 10%). (a) 279 of 280, (b) 19%, (c) ok, (e) 0 of 178 all pass | No |

**Diagnosis.** The verdict compared two shares with no regard for how many tasks they came from. A 10% canary over 4 slices is 100 tasks; at a 5% hang rate that is 5 traps give or take 2, and the main group's natural tail at the tightened deadline adds a few tenths of a point. Sampling noise alone moves the cost-against-benefit comparison across the DEGRADED line about one review in three, and the review repeats every 4 slices, so almost every correctly tightened runaway was eventually declared DEGRADED (70 of 80) and a restore request filed. In all, 355 restore requests on 400 tightened agent-seeds: the layer asked a person to undo nearly everything, which is the opposite of relocating human attention. The gross cases were right (slow-from-start 60 of 60 within 6 slices; inflating attackers mostly DEGRADED at the first look, which the time currency makes correct); the marginal ones were noise.

**Change made after seeing the result.** Verdicts now carry confidence bounds (normal approximation, z = 2, rule-of-three upper bound for a zero count) and are computed over every slice since the tightening (capped at 32), not the last 4: DEGRADED only if the lower bound of the collateral's cost exceeds the upper bound of the maximum benefit; IMPROVED only if the upper bound of the cost is under the lower bound of the maximum benefit (`improved_ratio` now 1.0, "confidently net positive"); UNCHANGED when the bounds do not separate. The window change trades away the episode-end detection of P2: a cumulative window dilutes the signal that a tightening has stopped buying anything.

**What the arithmetic says to expect, written before the rerun.** With a 10% canary, confirming harm needs a collateral of several points (slow-from-start: 7 to 18 points, caught within a few slices; a 3x regression: 6 points, caught after a few more; ho-healthy-1 at 2 points: marginal, after 20 or more slices; healthy-tail at half a point: never). Confirming benefit is harder still, because one newly killed task costs the whole tightened deadline while one hang saved earns only the cut: at the first 10% step the collateral must be shown to be under a ninth of the hang share, which needs tens of thousands of canary tasks. So IMPROVED should be rare and most correct tightenings should read UNCHANGED, which is the honest answer at this canary volume.

**Pre-registered predictions for the bounded validator, fresh seeds 4001-4020.**

| # | Prediction |
|---|---|
| P1' | Sustained runaways: DEGRADED while hanging in at most 10% of agent-seeds (from 70 of 80); IMPROVED ever in at most 20%; the rest UNCHANGED |
| P2' | Episode runaway: DEGRADED after the episode in at most 30% (the predicted loss) |
| P3' | Slow-from-start: DEGRADED with a restore request within 6 slices in 100%. Falsely released 3x and 4x regressions and 3.5x drift: DEGRADED eventually in at least 60%. ho-healthy-1: DEGRADED ever between 20% and 80%, within 6 slices at most 20%. healthy-tail: DEGRADED ever at most 30% |
| P4' | Inflating attackers: DEGRADED ever at least 50% |
| P5' | Low-and-slow: not DEGRADED in at least 80% |
| P6' | Restore requests on at most 40% of tightened agent-seeds (from 89%), and on at most 10% of sustained-runaway agent-seeds |
| P7' | Criterion 10 still FAILS, on the wrong-tightening half of (d) (healthy-tail and ho-healthy-1 cannot be resolved within 6 slices at this volume); the runaway half of (d) passes |

### Round 5, second result: the bounded validator (2026-10-14; fresh seeds 4001-4020; `experiments/stack_trace_multiseed_results_validated_bounded.json`)

| # | Prediction | Result | Held? |
|---|---|---|---|
| P1' | Sustained runaways DEGRADED while hanging at most 10%; IMPROVED ever at most 20%; the rest UNCHANGED | DEGRADED while hanging **7 of 80** (from 70 of 80); IMPROVED ever 13 of 80; first verdict UNCHANGED 77 of 80 | Yes |
| P2' | Episode runaway DEGRADED after the episode at most 30% | 1 of 20 | Yes (the predicted loss) |
| P3' | Slow-from-start DEGRADED within 6 slices in 100% | within 6: **48 of 60**; ever 60 of 60. The review runs 4 slices after the *last* tightening, and these agents tighten three times in their first three slices, so the first verdict lands at slice 6 or 7 | No |
| P3' | Falsely released 3x and 4x regressions and 3.5x drift DEGRADED eventually in at least 60% | **3 of 28**. They are released at slices 20 to 28; the cumulative window has 4 to 12 slices left before the trace ends and the bounds do not close in time | No |
| P3' | ho-healthy-1 DEGRADED ever 20% to 80%, within 6 slices at most 20% | ever **20 of 20** (above the range: the cumulative window gets there by slice 20 to 30); within 6: 1 of 20 | Half |
| P3' | healthy-tail DEGRADED ever at most 30% | **9 of 11** (82%): the same cumulative power, late | No, in the right direction |
| P4' | Inflating attackers DEGRADED ever at least 50% | 73 of 160 (46%) | No, narrowly |
| P5' | Low-and-slow not DEGRADED in at least 80% | 13 of 20 (65%) | No |
| P6' | Restore requests at most 40% of tightened agent-seeds; at most 10% on sustained runaways | 182 of 408 (45%); sustained runaways 7 of 80 (9%) | Half |
| P7' | Criterion 10 still fails on the wrong-tightening half of (d), passes the runaway half | **FAIL**: (d) wrong tightenings caught within 6 slices 54 of 128; runaways DEGRADED while hanging 8 of 100. (a) 280 of 280, (b) 19%, (c) ok, (e) 0 of 179 | Yes |

Verdicts overall: UNCHANGED 1,176, IMPROVED 412, DEGRADED 219. Hung time saved on sustained runaways 18% to 23% with the canary (20% to 26% without).

**What the validation layer is, after two runs.** With confidence bounds it no longer asks a person to undo correct tightenings (7 of 80 sustained runaways instead of 70). It confirms the gross wrong tightenings (every slow-from-start agent, every seed) and, given 20 or more slices, the wide-tail ones (ho-healthy-1 20 of 20, healthy-tail 9 of 11). It cannot confirm harm quickly: the 6-slice bar in criterion 10 was missed by one or two slices on the slow-from-start agents and by a long way on everything marginal, and a regression released late in the trace is never resolved. It almost never confirms benefit: IMPROVED needs the collateral shown to be under about a ninth of the hang share at the first 10% step, which a 10% canary does not have the volume for; "UNCHANGED" on a correct tightening means "cannot tell", not "no effect". And it is blind to intent by design: an attacker's inflated-but-completing tasks count as collateral, so 46% of inflating attackers get a restore request a person then has to refuse.

**Criterion 10 stands as FAIL** on the speed half of (d). The bar was written before the layer existed and is kept; the arithmetic says what would pass it: a canary of 30% or more, or windows of 2,000 canary tasks before the first verdict, each of which costs saving or time. That trade is a policy choice for whoever runs this, not something to tune here on these seeds.

**Change log for this round, for the record.** One change after seeing results (the bounds and the cumulative window), checked on fresh seeds; its predictions held on the runaway side and missed on the speed side, as written above. No threshold in the recommended policy was changed.

## Sixth round: pricing the canary, and per-class rate thresholds (design and predictions written before any code ran)

**Two open items, one experiment.** Round 5 left the validation layer failing criterion 10 on confirmation speed, with the arithmetic saying a larger canary would resolve verdicts faster at the price of saving. Round 4 left the 1% rate threshold as a workload parameter with labelled agents on both sides of it. Both are priced here on the same fresh seeds (5001-5020), three rosters, so the trade-offs can be read off one table.

**Canary share.** The validated policy at canary shares of 10% (as before), 20% and 30%. Everything else unchanged.

**Per-class thresholds.** The traces carry a `team` column (core, batch, web, ml, data, ext). An operator who knows a class's natural tail sets its threshold above it; an operator who distrusts a class sets it lower. The setting used, fixed now: core 2% (its wide-tailed agents sit at 0.6% and 1.5%), ext 0.5% (external agents get less benefit of the doubt), every other team 1%. This is operator knowledge, not learned, and it is the cheap version of the fix; it is fair only to the extent an operator really would know a class's tail, which is the assumption being priced. Run at each canary share.

**Predictions.**

| # | Prediction |
|---|---|
| Q1 | Criterion 10(d), wrong tightenings confirmed within 6 slices: rises from about 42% at 10% to at least 60% at 20% and at least 75% at 30%. Slow-from-start agents reach 60 of 60 within 6 at 30% |
| Q2 | Hung-task time saved on sustained runaways falls by about the canary share: roughly 23%, 20%, 18% at 10, 20, 30% |
| Q3 | Correct runaway tightenings wrongly declared DEGRADED stays at or under 10% at every share; IMPROVED confirmations on sustained runaways rise with share, to at least 30% of agent-seeds at 30% |
| Q4 | Per-class thresholds: ho-healthy-1 and healthy-tail tightened in 0 of 20 seeds each (from 20 and 8); the low-and-slow attacker caught in 20 of 20 with a first slice no later than median 3 (from 19 of 20, median 4.5); every other agent within 15 points of its flat-threshold share |
| Q5 | Criterion 10 as a whole: passes at 30% canary with per-class thresholds **only if** the wrong tightenings that remain are mostly slow-from-start agents; the late-released regressions (3 to 7 per roster-set, released at slices 20 to 28) cannot be confirmed in 6 slices at any share, so the (d) wrong-half lands between 65% and 85% and the criterion is a coin flip. Recorded as the honest expectation, not a bar |
| Q6 | The price of per-class thresholds, which the roster cannot show: a core-team runaway at 1.5% hangs would be missed under a 2% core threshold. No such agent exists in the traces; the point is recorded so the "0 false tightenings" number is not read as free |

**What would change my mind.** If the 30% canary does not reach 60% on Q1, the layer's speed problem is not volume and the verdict design is wrong. If per-class thresholds move any runaway agent's detection, the class assignment is doing more than setting a tail allowance.

### Round 6, result (2026-10-14; seeds 5001-5020; `experiments/stack_trace_multiseed_results_round6.json`)

A first attempt crashed: the per-slice trap tally skipped traps the policy did not act on (held during a review hold, or expected load) while the canary count did not, so the main share went negative and the standard error took a square root of a negative number. Fixed (every deadline trap of a slice is tallied before the policy filters; regression test `TestSliceTally`), rerun on the same seeds.

| Policy | Wrong tightenings confirmed within 6 slices | Slow-from-start within 6 | Runaways falsely DEGRADED | Runaways IMPROVED | Hung time saved | ho-healthy-1 tightened | healthy-tail tightened | Low-and-slow caught | Restore requests |
|---|---|---|---|---|---|---|---|---|---|
| flat 1%, canary 10% | 47 of 122 (39%) | 38 of 60 | 3 of 80 | 7 of 80 | 22.2% | 20 of 20 | 7 of 20 | 17 of 20 | 176 of 399 |
| flat 1%, canary 20% | 62 of 122 (51%) | 54 of 60 | 3 of 80 | 23 of 80 | 19.7% | 20 of 20 | 7 of 20 | 17 of 20 | 178 of 399 |
| flat 1%, canary 30% | 67 of 122 (55%) | 59 of 60 | 1 of 80 | 28 of 80 | 16.4% | 20 of 20 | 7 of 20 | 17 of 20 | 180 of 399 |
| per-class, canary 10% | 41 of 106 (39%) | 38 of 60 | 3 of 80 | 7 of 80 | 22.2% | 11 of 20 | 0 of 20 | 20 of 20 | 158 of 386 |
| per-class, canary 20% | 58 of 106 (55%) | 54 of 60 | 3 of 80 | 23 of 80 | 19.7% | 11 of 20 | 0 of 20 | 20 of 20 | 159 of 386 |
| per-class, canary 30% | 68 of 106 (64%) | 59 of 60 | 1 of 80 | 28 of 80 | 16.4% | 11 of 20 | 0 of 20 | 20 of 20 | 173 of 386 |

(The recommended policy without a canary saved 24.6% on the same seeds.)

| # | Prediction | Held? |
|---|---|---|
| Q1 | Confirmation within 6 slices at least 60% at 20% and 75% at 30%; slow-from-start 60 of 60 at 30% | **No**: 51% and 55% (64% with per-class); slow-from-start 59 of 60 |
| Q2 | Saving about 23, 20, 18% | Yes: 22, 20, 16% |
| Q3 | Runaways falsely DEGRADED at most 10% at every share; IMPROVED at least 30% at 30% | Yes: 4%, 4%, 1%; 35% |
| Q4 | ho-healthy-1 and healthy-tail 0 of 20; low-and-slow 20 of 20 with median first slice at most 3; nothing else moves more than 15 points | Half: healthy-tail 0 of 20 and low-and-slow 20 of 20 at median 2, but ho-healthy-1 **11 of 20** (its 1.5 to 1.8% tail with the daily cycle puts local 2,000-task windows over 2% about half the time); no other agent moved more than 15 points |
| Q5 | Criterion 10 a coin flip at 30% with per-class | No: FAIL at every configuration, (d) wrong-half 39 to 64% |

**By my own pre-stated test, the speed problem is not only volume.** The 30% canary took the gross cases to 59 of 60 within 6 slices but the aggregate to 55 to 64%. What remains, read from the per-seed data: the slow-from-start agents' first review lands 5 or 6 slices after their first tightening (56 of 60 at 5, 4 at 6), because the review is scheduled 4 slices after the **last** tightening and these agents tighten three times in their first three slices (the breaker's allowance). A verdict that is not reached at that first look gets its second look 4 slices later, outside the 6-slice bar; at a 10% canary that is what happened on 22 of 60, at 30% on 1 of 60. So the cadence costs the bar two slices of evidence at every share. The other wrong tightenings are late-released regressions (released at slices 20 to 28, confirmed 1 of 25 within 6) and ho-healthy-1 (confirmed within 6 in 1 of 11). Per-class thresholds did what they were set to do for the 0.6% tail and the low-and-slow attacker, and only half the job for the 1.5% tail, because a threshold set at 2% against a 1.6% mean leaves the noise to decide.

**Change to make, with its check pre-registered (round 7).** Schedule the review from the **first** tightening of an episode and do not push it out when the breaker's second and third steps follow. The canary already runs from the first tightening, so no evidence is lost; the verdict just arrives sooner, on a window that may straddle one more tightening (the bounds absorb that). Fresh seeds 6001-6020, per-class thresholds, canary 10% and 30%. Predictions:

| # | Prediction |
|---|---|
| R1 | Slow-from-start confirmed within 6 slices: 60 of 60 at both shares (from 38 and 59) |
| R2 | Wrong tightenings confirmed within 6 at 30%: at least 72% (from 64%); at 10%: at least 50% (from 39%) |
| R3 | Runaways falsely DEGRADED stays at or under 10% at both shares (earlier reviews see fewer samples; the bounds should hold) |
| R4 | Late-released regressions and ho-healthy-1 do not move: they are not a cadence problem |
| R5 | Criterion 10 still FAILS at both shares on (d)'s wrong-half, by the late regressions and ho-healthy-1 alone |

**Kernel check of the canary.** The drift-suppression trace replayed through the real Rust kernel with the canary (`governed_workload --canary-share 0.1 --canary-ns`) against the Python model, validated policy: identical tightening decisions, identical restore requests and identical verdict sequences on 6 of 7 agents. On `inflate-first-1` both tightened first at slice 9, both three times to 72.9 ms, both ended with a restore request; but the kernel's reviews fell at slices 20, 24 and 28 (all DEGRADED) and the model's at 16, 20, 26 and 30 (UNCHANGED three times, then DEGRADED). The last tightening step fell on different slices, which moved the review schedule, and the kernel's windows then read DEGRADED where the model's read UNCHANGED (the kernel's run also killed 57 more legitimate tasks, 769 against 712). The step's timing is a borderline excess-release call that depends on the completed-duration quantiles, which with a canary include tasks completed under the original deadline; the exact divergence was not pinned down. The model is held to the kernel on decisions and outcomes; one agent's intermediate verdicts differed. Along the way the kernel's own defect was found: its limit client refused fractional values, which is why every tightened memory cap in the committed v1 run was ignored (0.9 of 512 MiB is not a whole number) while whole-numbered token and deadline limits were applied. Fixed in STACK-Kernel by flooring, which can only tighten.

### Round 7, result (2026-10-14; seeds 6001-6020; `experiments/stack_trace_multiseed_results_round7.json`): the cadence change did nothing, and was reverted

| # | Prediction | Result | Held? |
|---|---|---|---|
| R1 | Slow-from-start confirmed within 6 slices: 60 of 60 at both shares | 38 of 60 at 10%, 56 of 60 at 30%: the same as before. The first review still came exactly 5 slices after the first tightening on all 60 | **No** |
| R2 | Wrong tightenings within 6: at least 72% at 30%, at least 50% at 10% | 62% and 44% | No |
| R3 | Runaways falsely DEGRADED at or under 10% | 10 of 80 (12.5%) at 10%; 1 of 80 at 30% | No at 10% |
| R4 | Late regressions and ho-healthy-1 unchanged | 7 of 32 and 3 to 4 of 16: unchanged | Yes |
| R5 | Criterion 10 still fails on the wrong-tightening half | FAIL at both shares | Yes |

**Why it did nothing.** The diagnosis was wrong. A slow-from-start agent's three tightenings do not land in three slices; they land in its first slice (37 traps in 250 tasks, and the rate rule's window is the whole 250 tasks that early), so the episode's start is the same slice whichever step it is measured from. The 5-slice gap is simply `validate_after` (4 complete slices after the tightening slice) and the bar of 6 leaves room for exactly one look. At 10% that look has 100 canary tasks and confirms about two in three; the second look is at 9 and too late. So the bar is decided by the volume at the first look, which is the canary share, and the only cadence lever would be a shorter `validate_after`, which buys speed with the same evidence problem. The code change was reverted (same-seed control run recorded below); its unit test removed.

**What this round settles.** Criterion 10(d)'s 6-slice bar, at a 10% canary, asks the first look to confirm, and at this volume it confirms gross harm about two times in three and marginal harm almost never. A 30% canary confirms gross harm 56 to 59 times in 60 and costs a third of the saving. Neither clears the bar once the late-released regressions and the 1.5%-tail agent are in the denominator, and no cadence trick changes that. The honest options are the ones in `docs/DEPLOYMENT_CHECKLIST.md`: pick a canary share for the harm you fear, or rewrite the bar as a judgment call and say so.

**Same-seed control** (`..._round7_control.json`: seeds 6001-6020, per-class, 10% canary, reverted code): slow-from-start within 6 slices 39 of 60, wrong tightenings 47 of 108, runaways falsely DEGRADED **11 of 80**. So the 10 of 80 above was the seeds, not the change: on identical code the runaway false-DEGRADED share is 4% on seeds 5001-5020 and 14% on seeds 6001-6020. Criterion 10's "at most 10%" on that half sits inside seed-to-seed variation at 80 agent-seeds, which is one more reason not to read a pass or fail on it as settled by a single 20-seed run.

## Eighth round: gaming the canary (T16), predictions written before any code ran

**The attack.** The validation layer keeps every tenth task by sequence number on the original deadline and compares that group's trap share with the rest. The rule is public and the sequence number is the agent's own. An agent that hangs tasks but completes the ones it knows are canaries makes the main share high and the canary share near zero: the verdict reads DEGRADED, a restore request is filed for a person, and automatic tightening of that boundary is held until the person decides. The validation layer, built to catch wrong tightenings, becomes a way to get a right one undone and to freeze the ratchet meanwhile. Listed as untested in `p3-2-governance-research/experiments/adversarial-testbed.md`.

**The fix.** Choose canary tasks by a keyed hash of (key, agent, sequence number) with a key the agent cannot read, so membership is unpredictable from the agent's side. With the same hash in the Python model and the Rust example (64-bit FNV-1a over the UTF-8 bytes of `key|agent|seq`), the kernel check carries over. The share stays the same; only the choice of tasks changes.

**Roster `canary_v1`** (3 agents x 8,000 tasks, generated under each seed): `attacker-canary` hangs 5% of its non-canary tasks (sequence number not a multiple of 10) and never a canary task; `control-runaway` hangs 5% of all tasks; `healthy`. Validated policy, 10% canary, predictable selection against keyed selection. 20 fresh seeds (7001-7020).

| # | Prediction |
|---|---|
| T1 | Predictable selection: the attacker is tightened by the rate rule in 20 of 20 seeds, then receives a DEGRADED verdict with a restore request in at least 18 of 20, and its boundary enters the review hold. The layer is gamed |
| T2 | Predictable selection: the control runaway gets no restore request in at least 18 of 20 (as in round 5) |
| T3 | Keyed selection: the same attacker, still sparing tasks whose sequence number is a multiple of 10, gets no DEGRADED verdict in at least 18 of 20, and its hung-task saving is within 5 points of the control runaway's |
| T4 | Keyed selection changes nothing for the control runaway or the healthy agent |
| T5 | Not measured, argued: against keyed selection an agent has no better strategy than hanging uniformly, which is the runaway case the layer already handles. An agent that can read the key, or observe which of its tasks got the longer deadline before deciding to hang, is outside this fix; the kernel must not reveal the deadline to the task |

**What would change my mind.** If T1 fails (the attacker is not gamed under predictable selection), the attack does not exist and the keyed selection is unnecessary complexity. If T3 fails, the hash is not hiding membership and the fix is wrong.

### Round 8, result (2026-10-14; seeds 7001-7020; `experiments/stack_trace_multiseed_results_round8.json`)

| Policy | attacker-canary | control-runaway | healthy |
|---|---|---|---|
| recommended (no canary) | tightened 20 of 20, saved 25.9% | tightened 20 of 20, saved 26.0% | untouched |
| validated, predictable canary | tightened 20 of 20; **DEGRADED on 140 of 140 reviews; restore request 20 of 20; review hold 20 of 20**; saved 25.9% (its canaries never hang, so the canary costs it nothing) | no DEGRADED; 5 IMPROVED, 135 UNCHANGED; saved 23.4% | untouched |
| validated, keyed canary | tightened 20 of 20; DEGRADED 4 of 140 reviews, restore request 1 of 20; 20 IMPROVED; saved 23.2% | DEGRADED 1 of 140, restore 1 of 20; 19 IMPROVED; saved 23.3% | untouched |

| # | Prediction | Held? |
|---|---|---|
| T1 | Predictable: attacker tightened 20 of 20, DEGRADED with restore in at least 18, review hold | Yes, on every review of every seed |
| T2 | Predictable: control runaway no restore in at least 18 | Yes, 20 of 20 |
| T3 | Keyed: attacker no DEGRADED in at least 18; saving within 5 points of the control | Yes: 19 of 20; 23.2% against 23.3% |
| T4 | Keyed changes nothing for the control and the healthy agent | Yes (the control's 1 of 20 is the seed noise seen in rounds 5 to 7) |

**What this establishes.** With a predictable canary the validation layer is a loosening lever: an agent that completes exactly its canary tasks and hangs the rest gets a correct tightening declared harmful on every review, a restore request filed to a person, and automatic tightening of its boundary frozen while that request waits. A person who trusts the verdict hands the attacker its full budget back. Keyed selection closes it: the same attacker, still sparing every tenth task, is treated like the honest runaway, and the honest runaway is unchanged. The key is now `TrapPolicy(canary_key=...)`, passed to the kernel in the canary plan, and `docs/DEPLOYMENT_CHECKLIST.md` item 6 says to set it. Kernel side: `governed_workload --canary-key`, same hash (reference vectors in `tests/test_trace_sim.py`).

**Limits.** An agent that can read the key, or that can tell which deadline a task is running under before deciding whether to hang it, defeats this; the kernel must not expose the deadline to the task. A share of 10% chosen by hash is still a share of 10%: everything round 5 said about confirmation speed and benefit still holds. Not measured: an attacker that probes membership by hanging a task and watching whether it is killed at 72.9 or 100 ms, then adapts per task; that needs per-task observability the trace model does not represent, and is the next thing to rule out if the kernel ever leaks it.

## Ninth round: the ratchet against a two-way adaptive limiter (design and predictions written before any code ran)

**The question.** The nearest existing thing is a two-way adaptive limiter: lower the limit on trouble, raise it again when things look calm (TCP's AIMD, Netflix `concurrency-limits`, GitLab's Gitaly limit). The thesis says the one-way version with a human gate is safer. That has not been tested head to head. This round runs both on the same traces, same seeds, and measures what each attacker gets and what each legitimate agent pays.

**The baseline.** `aimd`: per agent, deadline only. Decrease: the same trigger as the ratchet's rate rule (at least 5 traps and at least 1% of the last 2,000 tasks), the same step (x0.9), the same 20% floor. Increase: after any slice whose trap share is under 0.5%, raise the limit by 2% of the original, never above the original. No breaker and no human: those are the ratchet's features, and the point is to see what they buy. `aimd_fast`: the same with a 10% increase per calm slice. Against them: `recommended` (the ratchet with rate, drift and excess rules) and `keyed_c10` (the same with the validation layer and a keyed canary).

**Roster `pulse_v1`** (8 agents x 8,000 tasks per seed): `attacker-pulse-2`, `-4`, `-8` hang 5% of tasks during one slice out of every 2, 4 or 8 and behave in between (an attacker that earns its budget back); `runaway-steady` (5% always); `attacker-lowslow` (0.8% always); `regressed-0` (3x slower from task 4,000, honest); `healthy-0`; `healthy-tail` (0.6% natural tail). Also the full `v1` roster for the broad comparison. 20 fresh seeds (8001-8020).

**Predictions.**

| # | Prediction |
|---|---|
| Z1 | Pulse attackers: under `aimd` the limit recovers between pulses, so hung-task time saved is at most half the ratchet's on the period-8 attacker (ratchet at least 20%, `aimd` at most 10%); `aimd_fast` worse than `aimd` |
| Z2 | Sustained runaway: `aimd` saves **more** than the ratchet (at least 50% against about 26%), because without a breaker it goes to the floor. This is the breaker's price and it is recorded as such, not hidden |
| Z3 | Honest regression (3x slower, never calm again): `aimd` drives it to the floor; its legitimate timeouts rise by at least 30 points. The ratchet holds it (drift rule) at a cost under 1 point and files one restore request |
| Z4 | Healthy agents: untouched by all policies; `healthy-tail` (0.6%): `aimd` tightens and recovers repeatedly (oscillates), ratchet tightens in about 40% of seeds and holds |
| Z5 | Low-and-slow: both tighten late; neither saves more than 20%; `aimd` gives the budget back in calm windows and saves less |
| Z6 | On the full `v1` roster, summed over should-not agents, `aimd`'s legitimate cost is higher than the ratchet's; summed over should-tighten agents, `aimd` saves more on sustained ones and less on intermittent ones |

**What this would establish.** If Z1 and Z3 hold with Z2, the asymmetry's value is specific and measurable: an attacker cannot earn budget back by behaving, and a legitimate regression is not ground down, at the price of a capped saving on sustained attacks. If Z1 fails, the one-way rule is not where the value is and the claim should say so.

**Change before any result was scored.** The first build of `aimd` reused the ratchet's 2,000-task trigger window for its decrease, so while a pulse was anywhere in that window the increase could never fire (the smoke run showed zero increases), which would have made the comparison a ratchet-to-the-floor against a ratchet. A two-way limiter reacts to its current signal, so the baseline now decreases when the current slice has at least 3 traps and at least 1% of its tasks trapped, and increases when the slice's share is under 0.5%. The first run's results file was deleted unread. Predictions unchanged.

### Round 9, result (2026-10-14; seeds 8001-8020; `experiments/stack_trace_multiseed_results_round9.json`)

Pulse roster, hung-task time saved against static / legitimate-timeout cost in points (20 seeds):

| Agent | ratchet (recommended) | ratchet + validation (keyed 10%) | aimd (+2% per calm slice) | aimd_fast (+10%) |
|---|---|---|---|---|
| attacker-pulse-2 | 25% / 0.4 | 22% / 0.4 | **54%** / 23.9 | 13% / 2.9 |
| attacker-pulse-4 | 23% / 0.5 | 21% / 0.4 | 24% / 5.4 | 0% / 0.0 |
| attacker-pulse-8 | 16% / 0.4 | 15% / 0.3 | **0%** / 0.0 | 0% / 0.0 |
| runaway-steady | 26% / 0.5 | 23% / 0.4 | **65%** / 31.0 | 65% / 31.0 |
| attacker-lowslow (0.8%) | 13% / 0.2 | 11% / 0.1 | **53%** / 22.6 | 41% / 16.0 |
| regressed-0 (honest, 3x) | tightened 2 of 20, 0.2 | 2 of 20, 0.2 | 20 of 20, **24.3** | 20 of 20, 24.3 |
| healthy-0 | untouched | untouched | untouched | untouched |
| healthy-tail (0.6%) | 8 of 20, 0.3 | 8 of 20, 0.1 | 20 of 20, **26.5** | 20 of 20, 24.9 |

Full v1 roster, pooled: hung time saved on deadline attackers 23.7% (ratchet), 21.2% (ratchet + validation), **56.5%** (aimd), 55.8% (aimd_fast); on sustained runaways 25.6% against 64.1%; on the episode runaway 16.7% against 13.9%. Mean legitimate cost over the 16 should-not agents: **1.99 points (ratchet) against 17.75 (aimd)**, worst single agent 19.7 against 67.7.

| # | Prediction | Result | Held? |
|---|---|---|---|
| Z1 | Period-8 pulser: aimd at most 10%, ratchet at least 20%; aimd_fast worse | aimd 0%, aimd_fast 0%; ratchet **16%** | Half: the direction held, the ratchet's number did not. And on periods 2 and 4 the result reversed: aimd saved 54% and 24%, because a 10% decrease per pulse against a 2% increase per calm slice nets downward unless the quiet stretch is long |
| Z2 | Sustained runaway: aimd saves more (at least 50% against about 26%) | 65% against 26% | Yes |
| Z3 | Honest regression: aimd at least 30 points, ratchet under 1 | 24.3 against 0.2 | Direction yes, number no |
| Z4 | Healthy untouched; healthy-tail oscillates under aimd, ratchet about 40% | Untouched; healthy-tail **ground to the floor** under aimd on every seed (26.5 points), not oscillating: three or more traps in a 250-task slice happens about one slice in five at a 0.6% tail, and each costs 10% while a calm slice returns 2%; ratchet 8 of 20 | Half |
| Z5 | Low-and-slow: neither over 20%; aimd less | aimd **53%**, ratchet 13% | **No** |
| Z6 | v1 pooled: aimd's legitimate cost higher; aimd saves more on sustained, less on intermittent | 17.75 against 1.99; 64% against 26%; 13.9% against 16.7% | Yes |

**What this establishes, which is not what the thesis said.** The one-way rule by itself is not what protects anyone. A two-way limiter with no breaker goes to the floor on anything that keeps tripping it, honest or not, and so it saves two to four times more hung time than the ratchet *and* ruins every honest agent with a tail or a regression. The ratchet's measured advantage is availability for honest agents, bought with the breaker (a cap on automatic steps), the drift hold, and the human gate; its measured price is a saving capped at the breaker's arithmetic. The specific "attackers cannot earn budget back" benefit exists but is narrow: it shows only when an attacker's quiet stretch is long enough for the increase to outrun the decrease (period 8 here), and even there the ratchet saves 16%, not 20%.

**The question this leaves, pre-registered now as round 9b.** Is it the one-way rule or the cap that protects honest agents? A two-way limiter with the breaker's cap (`aimd_capped`: the same `aimd`, floor at 72.9% of the original instead of 20%) isolates them. Same seeds (8001-8020), same rosters. Predictions: (B1) its legitimate cost on the v1 should-not agents is within 2 points of the ratchet's 1.99, and on regressed-0 and healthy-tail under 3 points each, so the cap, not the direction, is what protects honest agents; (B2) its saving on sustained runaways equals the ratchet's cap (about 26%); (B3) on the period-8 pulser it saves at most 8%, under the ratchet's 16%, because it gives the budget back, which is the one-way rule's only unique contribution; (B4) on the low-and-slow attacker it saves within 5 points of the ratchet's 13%.

### Round 9b, result (2026-10-14; same seeds 8001-8020; `experiments/stack_trace_multiseed_results_round9b.json`)

| Agent | ratchet | aimd_capped (two-way, floor at the breaker's cap) |
|---|---|---|
| attacker-pulse-2 | 25% / 0.4 | 22% / 0.4 |
| attacker-pulse-4 | 23% / 0.5 | 15% / 0.3 |
| attacker-pulse-8 | 16% / 0.4 | **0%** / 0.0 |
| runaway-steady | 26% / 0.5 | 25% / 0.4 |
| attacker-lowslow | 13% / 0.2 | 22% / 0.4 |
| regressed-0 (honest) | 2 of 20 tightened, 0.2 | 20 of 20, **8.3** |
| healthy-tail | 8 of 20, 0.3 | 20 of 20, 1.3 |
| v1 pooled: saved on deadline attackers / should-not cost | 23.7% / 1.99 | 23.6% / 3.96 |

| # | Prediction | Result | Held? |
|---|---|---|---|
| B1 | Legitimate cost within 2 points of the ratchet's pooled; regressed-0 and healthy-tail under 3 each | pooled 3.96 against 1.99 (1.97 apart); healthy-tail 1.3; regressed-0 **8.3** | Half |
| B2 | Sustained saving equals the cap | 25.4% against 25.6% | Yes |
| B3 | Period-8 pulser at most 8%, under the ratchet's 16% | 0% | Yes |
| B4 | Low-and-slow within 5 points of the ratchet | 22% against 13%: the capped two-way limiter saves **more**, because its per-slice trigger fires on an 0.8% hang rate more often than the ratchet's 2,000-task rate rule | No |

**The decomposition, which is the result of the round.** Take the ratchet apart into its four features and the same seeds say what each buys:

- **The cap** (three automatic steps, then stop) is what protects honest agents from being ground to the floor: it takes the pooled legitimate cost from 17.75 points to 3.96, and healthy-tail from 26.5 to 1.3. It is also what caps the saving at about 26% on anything sustained. A two-way limiter with the same cap matches the ratchet on sustained runaways to within a point.
- **The drift hold** (and the human gate behind it) is what protects the honest *regression*: 8.3 points under the capped two-way limiter against 0.2 under the ratchet. A cap alone does not know the agent is slow for an honest reason.
- **The one-way rule itself** has one measurable, narrow contribution: an attacker who can wait out the limiter's recovery keeps its budget under a two-way limiter and loses it under the ratchet (period 8: 0% against 16%; period 4: 15% against 23%). Against sustained or frequent attackers the direction makes no difference, and the slower-firing rate rule the ratchet uses even costs it 9 points on the low-and-slow attacker.
- **The human gate** has no measured benefit in these traces because no human acts in them; what it does is turn the cap's "stop" into "stop until someone looks", which is a property of the operation, not of the replay.

**Where this leaves the thesis.** "Automation may only tighten" is not, by itself, the source of safety. Safety against grinding honest agents comes from bounding automation (the cap) and from an interpretation rule (the drift hold) that a two-way limiter could also have. What the direction uniquely gives is that an adversary cannot restore its own budget by behaving for a while, which is real but narrow, and the price is that nobody but a person can restore anyone's. The claim should now be stated that way, and the comparison table above belongs in the paper's evaluation, not in a footnote.

## Tenth round: making the decomposition defensible (design and predictions written before any code ran)

Three checks a reviewer would ask for, run before any writing.

**10a. A two-way limiter with the cap and the drift hold** (`aimd_capped_drift`): `aimd_capped` plus the ratchet's drift rule (baseline = median of the first five slices' completed-duration medians; no decrease while the latest median exceeds 1.5x baseline). No excess release, deliberately: that rule is the ratchet's answer to T15, and the question is what transfers.

| # | Prediction |
|---|---|
| D1 | Honest regression (`regressed-0`): cost at most 1 point (from 8.3 under `aimd_capped`; 0.2 under the ratchet) |
| D2 | v1 pooled should-not cost within 1 point of the ratchet's 1.99 |
| D3 | Period-8 pulser still 0% saved (the direction's contribution is untouched by the drift rule); sustained runaway about 25% |
| D4 | The inflate-first attacker in v1 (`attacker-inflate`, T15): saving falls to at most 5% (from 26% under the ratchet, which has the excess release), because the drift hold without the release holds it for the whole attack. So the interpretation rules transfer, and so does their weakness, and the ratchet's advantage there is the excess rule, not the direction |

**10b. When does the direction matter? A formula, then a sweep.** Under a two-way limiter, an attacker that hangs during one slice in every `period` and behaves otherwise loses about 10% per pulse and regains `increase` per calm slice. Its budget recovers between pulses when `(period - 1) x increase >= 0.10`. Prediction D5: `aimd_capped` saves at least 8 points less than the ratchet exactly in the cells where that inequality holds, and within 8 points of the ratchet elsewhere, allowing one cell either side of the boundary. Sweep: periods 2, 4, 8, 16 (a `attacker-pulse-16` is added at the end of the pulse roster, which leaves every other agent's seed unchanged) x increase 1%, 2%, 5%, 10%. From the formula the direction should matter at: 1% only for period 16; 2% for 8 and 16; 5% for 4, 8 and 16; 10% for 2 (boundary), 4, 8 and 16. Round 9 already gave three cells consistent with this (2%: period 8 at 0% against 16%; 10%: periods 4 and 8 at 0%).

**10c. Replication.** The whole comparison (ratchet, `aimd`, `aimd_capped`, `aimd_capped_drift`) on fresh seeds 9001-9020 and on the held-out roster as well as the pulse roster. Prediction D6: the three decomposition statements hold on both (cap protects honest agents, drift hold protects the honest regression, direction matters only for patient pulsers), with every number within 5 points of the round-9 values on the pulse roster.

**Also before writing:** `docs/references.bib` gains the prior art the novelty check named (pledge/unveil, seccomp, AIMD, Netflix concurrency-limits, Kayenta, Rehak 2009). Not a prediction, a debt.

### Round 10, results (2026-10-14; `..._round10ab.json` on seeds 8001-8020, `..._round10c.json` on seeds 9001-9020)

**10a, the capped two-way limiter with the drift hold.**

| # | Prediction | Result | Held? |
|---|---|---|---|
| D1 | Honest regression cost at most 1 point | 0.00 (ratchet 0.17) | Yes |
| D2 | v1 pooled should-not cost within 1 point of the ratchet | **1.99 against 1.99** (cap alone: 3.96) | Yes |
| D3 | Period-8 pulser still 0%; sustained about 25% | 0% (ratchet 16%); 25% (ratchet 26%) | Yes |
| D4 | `attacker-inflate` in v1 saved at most 5% without the excess release | 25% (ratchet 26%) | **No, and the prediction was aimed at the wrong agent**: that attacker hangs from its first slice and spends the breaker before it starts inflating (round 1 noted this), so the drift hold never engages. The agents that inflate *first* are in the held-out roster, where the predicted effect appears: `ho-inflate-mild` 19% under the ratchet against **0%** under cap plus drift, `ho-inflate-strong` 21% against 0%, `ho-inflate-ramp` 16% against 8% |

**10b, the formula.** Cells of hung time saved by the capped two-way limiter, against the ratchet in the first column; `*` where `(period - 1) x increase >= 0.10` says the attacker recovers its budget; `D` where the limiter saved at least 8 points less than the ratchet.

| period | ratchet | 1% | 2% | 5% | 10% |
|---|---|---|---|---|---|
| 2 | 25% | 23% | 22% | 20% | 6% *D |
| 4 | 23% | 19% | 15% D | 0% *D | 0% *D |
| 8 | 16% | 5% D | 0% *D | 0% *D | 0% *D |
| 16 | 10% | 0% *D | 0% *D | 0% *D | 0% *D |

D5: every cell the formula marks is a D (10 of 10), every cell it does not mark is not, except the two boundary-adjacent cells the prediction allowed (period 4 at 2%: 6% recovery, 8-point drop; period 8 at 1%: 7% recovery, 11-point drop). **Held.** The direction's value is a function of two numbers an operator knows: how long an attacker can afford to wait, and how fast the limiter gives budget back.

**10c, replication on fresh seeds and on the held-out roster** (pooled legitimate cost on should-not agents; hung time saved on attackers):

| | ratchet | aimd (no cap) | aimd + cap | aimd + cap + drift |
|---|---|---|---|---|
| pulse roster, pooled cost | 0.12 | 17.04 | 3.23 | 0.55 |
| pulse roster, honest regression | 0.2 | 24.6 | 8.4 | 0.3 |
| pulse roster, period-8 / period-16 pulser saved | 17% / 10% | 0% / 0% | 0% / 0% | 0% / 0% |
| pulse roster, sustained runaway saved | 26% | 65% | 26% | 26% |
| held-out roster, pooled cost | 2.01 | 21.90 | 4.19 | 1.94 |
| held-out, 4x regression / 3.5x drift cost | 0.2 / 0.1 | 31.4 / 26.8 | 9.0 / 6.3 | 0.0 / 0.0 |
| held-out, inflate-first attackers saved (mild / strong / ramp) | 19 / 21 / 16% | 53 / 73 / 67% | 25 / 27 / 27% | **0 / 0 / 8%** |

D6: all three statements hold on both rosters, and every pulse-roster number is within 5 points of round 9. **Held.**

**The decomposition, final form.** On these workloads the ratchet equals: a cap (protects honest agents, caps the saving) plus a drift hold (protects honest regressions and drifts) plus an excess release (recovers the saving against attackers who inflate first, which the drift hold alone gives away entirely) plus the one-way rule (keeps the budget from an attacker who can wait, by the formula above). A two-way limiter with the first two matches the ratchet on every honest agent to within a point and on every sustained attacker; it loses to the ratchet only against patient pulsers and, without the excess rule, against inflate-first attackers. The human gate is the price of the direction: nobody but a person restores anyone's budget. That is the claim the project can defend.

## Eleventh round: robustness of the decomposition (design and predictions written before any code ran)

Five things a referee asks about the rounds 9 and 10 result, each answerable with the existing harness.

**11a. Other duration shapes and headroom** (roster `robust_v1`). The generator draws task durations from a lognormal with log-spread 0.5. Variants: `pareto` (heavy tail, shape 2.5, scaled to the same median) and `bimodal` (80% of tasks at the median, 20% at three times it). For each shape: a 5% runaway, a 0.6%-tail healthy agent, a 3x honest regression at task 4,000, and a period-8 pulser. Plus a `tight` agent: lognormal, median 50 ms under the 100 ms deadline, so about a 5% natural tail with no hangs, the known wide-tail limit under a different name. Policies: ratchet, `aimd`, `aimd_capped_drift`. Seeds 10001-10020.

| # | Prediction |
|---|---|
| R1 | Under both other shapes the three statements hold: `aimd`'s pooled legitimate cost at least three times `aimd_capped_drift`'s (cap); the honest regression at most 1 point under `aimd_capped_drift` (drift hold); the period-8 pulser at least 10% saved under the ratchet and at most 3% under `aimd_capped_drift` (direction). Sustained runaway within 5 points of 26% (ratchet) and at least 50% (`aimd`) |
| R2 | The `tight` agent is tightened by every policy and costs at least 10 points under every policy, the ratchet included. The decomposition does not help it; nothing in the trap stream can |

**11b. The pulse formula generalised** (roster `pulse2_v1`): period 8, active width `w` of 1 or 2 slices, hang intensity 2%, 5% or 10% during active slices. A two-way limiter loses about `1 - 0.9^w` per cycle and regains `(period - w) x increase`. Prediction R3: with the 2% increase, every width-1 agent shows the direction mattering (`aimd_capped` at least 8 points under the ratchet) and no width-2 agent does (14% recovery against 10% loss for width 1; 12% against 19% for width 2), at every intensity.

**11c. The cap as a dial.** The ratchet with `max_auto_tightenings` of 1, 2, 3, 5 and 8 on the v1 roster, seeds 8001-8020. Prediction R4: hung time saved on sustained runaways within 3 points of `1 - 0.9^k` (10, 19, 27, 41, 57%); pooled legitimate cost on should-not agents rises monotonically with k; the slow-from-start agents' cost rises roughly in proportion to the cut.

**11d. Human attention, counted.** Per agent-seed, the number of decisions a person is asked for: breaker holds (a held tightening waiting for acknowledgement) plus restore requests (drift and validation). Prediction R5: under the ratchet, at most one decision per tightened attacker (the breaker hold) and none for untouched healthy agents; under `aimd`, zero decisions and the nine-times-higher harm of round 9. The claim "relocates human effort" becomes a number: decisions per thousand tasks, by policy.

**11e. Intervals.** Bootstrap 95% intervals over seeds for the pooled cost and the sustained saving of each policy in 10c. Prediction R6: ratchet and `aimd` intervals do not overlap on either number; ratchet and `aimd_capped_drift` overlap on both.

### Round 11, results (2026-10-14; `..._round11ab.json` seeds 10001-10020, `..._round11cd.json` seeds 8001-8020)

**11a, other duration shapes** (saved% on attackers / legitimate cost in points):

| Agent | ratchet | aimd | aimd + cap + drift |
|---|---|---|---|
| pareto-runaway | 26% / 1.1 | 64% / 31.0 | 25% / 1.1 |
| pareto-tail | 1.0 | 36.7 | 1.2 |
| pareto-regressed | **5.8** | 44.8 | **8.6** |
| pareto-pulse-8 | 20% / 1.0 | 52% / 28.4 | **20%** / 1.0 |
| pareto pooled should-not | 3.39 | 40.77 | 4.90 |
| bimodal-runaway | 26% / 2.7 | 65% / 37.8 | 26% / 2.6 |
| bimodal-tail | 0.0 | 3.2 | 0.6 |
| bimodal-regressed | 0.0 | 26.3 | 0.0 |
| bimodal-pulse-8 | 19% / 2.2 | 4% / 2.3 | 2% / 0.3 |
| bimodal pooled should-not | 0.00 | 14.77 | 0.28 |
| tight (5% natural tail, no hangs) | tightened 20 of 20, 13.8 | 20 of 20, 67.0 | 20 of 20, 13.3 |

| # | Prediction | Result | Held? |
|---|---|---|---|
| R1 bimodal | cap, drift hold and direction statements all hold | cap 14.77 against 0.28; regression 0.0; pulser 19% against 2%; sustained 26% / 65% | Yes |
| R1 pareto | same | cap 40.77 against 4.90 and sustained 26% / 64% hold; **the drift statement fails** (regression 8.6 under cap plus drift, and 5.8 under the ratchet itself); **the direction statement fails** (pulser 20% against 20%) | Half, with a mechanism below |
| R2 | `tight` tightened by all, at least 10 points under all | 20 of 20 and 13.8 / 67.0 / 13.3 | Yes |

**Why Pareto breaks two of three.** A heavy tail puts traps into every slice, so the two-way limiter never sees a calm slice (share under 0.5%) and never increases: it behaves like a ratchet to its floor, grinding honest agents (pareto-tail 36.7 points) and, when capped, never giving a pulser its budget back, so the direction has nothing left to add. The drift hold also weakens: the completed-duration median moves less under a heavy tail than the trap share does, so a 3x regression is held less reliably and the excess release fires more often, which is why even the ratchet costs the honest Pareto regression 5.8 points. Both are statements about the signal, not the direction: **the formula's recovery term assumes calm slices exist, and the drift rule assumes the median tracks the slowdown.** Under heavy tails neither holds well.

**11b, width and intensity** (period 8, saved% ratchet against capped two-way at 2% increase):

| Agent | ratchet | capped | formula says | D (at least 8 under) |
|---|---|---|---|---|
| w1, 2% | 6% | 0% | direction matters | no (6 points: the ratchet itself saves little at this intensity) |
| w1, 5% | 18% | 0% | matters | **D** |
| w1, 10% | 20% | 1% | matters | **D** |
| w2, 2% | 15% | 13% | no | no |
| w2, 5% | 23% | 17% | no | no |
| w2, 10% | 24% | 16% | no | **D** (8 points exactly) |

R3: four of six cells as predicted; the two misses sit at the 8-point threshold (6 and 8 points). The sign is right in all six: width 1 differs by 6 to 19 points, width 2 by 2 to 8. **Held in direction, not at the stated threshold.**

**11c, the cap as a dial** (v1, seeds 8001-8020):

| k | sustained saved | formula 1 - 0.9^k | pooled should-not cost | slow-from-start cost |
|---|---|---|---|---|
| 1 | 9.7% | 10.0% | 0.58 | 4.2 |
| 2 | 18.2% | 19.0% | 1.23 | 9.4 |
| 3 | 25.6% | 27.1% | 1.99 | 15.4 |
| 5 | 37.6% | 41.0% | 3.77 | 29.6 |
| 8 | 50.1% | 57.0% | 6.61 | 52.0 |

R4: within 3 points of the formula for k up to 3; 3.4 and 6.9 points under it at 5 and 8, because each further step needs a fresh pattern and the later steps arrive late in the run. Cost monotone in k; slow-from-start cost proportional to the cut. **Held for the operating range, overstated by the formula at large k.** The dial is real: every step of the cap buys about 9 points of saving on sustained attackers and about 0.7 points of pooled honest cost (5 points on an agent slow from the start).

**11d, decisions a person is asked for** (v1, 500 agent-seeds):

| Policy | decisions | per 1,000 tasks | on the 100 deadline-attacker agent-seeds | on healthy agents | pooled should-not cost |
|---|---|---|---|---|---|
| ratchet (cap 3) | 216 | 0.054 | 156 | 0 | 1.99 |
| aimd | 0 | 0 | 0 | 0 | 17.75 |
| aimd + cap + drift | **0** | 0 | 0 | 0 | **1.99** |

R5: no decisions on healthy agents (held); 1.56 decisions per tightened attacker, not at most one (a breaker hold plus, often, a drift restore request). **The sobering row is the third:** the capped two-way limiter with the drift hold reaches the ratchet's honest-agent cost with no human in the loop at all, because its cap is a floor, not a hold waiting for acknowledgement. On this roster the ratchet's 216 decisions buy exactly the direction's narrow benefit (round 10) and the auditability of every change; they do not buy honest-agent safety, which the cap and the drift hold deliver unattended. This belongs in the paper as the price of the human gate, stated in decisions.

**11e, intervals** (bootstrap over seeds, 2,000 resamples, v1):

| Policy | pooled should-not cost | sustained saved |
|---|---|---|
| ratchet | [1.96, 2.02] | [25.6%, 25.7%] |
| aimd | [17.48, 18.00] | [63.8%, 64.5%] |
| aimd + cap + drift | [1.95, 2.03] | [25.3%, 25.5%] |

R6: ratchet and `aimd` do not overlap on either number (held). Ratchet and cap-plus-drift overlap on cost and **miss by 0.1 point on the saving** (25.5 against 25.6): not met as written; the intervals are tight because the structure is nearly deterministic across seeds, and a 0.2-point difference is real and immaterial.

**What round 11 adds to the defensible claim.** The decomposition holds under a bimodal shape and fails in two of three parts under a heavy tail, for stated reasons about the signal; the direction's formula is right in sign across widths and intensities and coarse at its threshold; the cap is a dial with a measured exchange rate; and the human gate costs about one decision per twenty thousand tasks on this roster for a benefit that a capped two-way limiter with the drift hold matches on honest agents without any decisions. The white paper should carry all of this, including the Pareto failure and the decisions table, because a referee who finds them first will not believe the rest.

### Round 12, pre-registered before any run (2026-10-14): the red team's findings put to the test

**Context.** Six hostile reviewers attacked the evidence (docs/RED_TEAM.md). Three findings change what rounds 9 to 11 mean and need fresh measurements rather than re-reading: the pulsers were caught in slice 0 by a rate rule whose window had not filled (B1); a two-way limiter with hysteresis on its increase matched or beat the ratchet everywhere (B2); the honest-agent results depend on deadline headroom (T1), and the saving metric is arithmetic while the net time is negative (T2). The model's completed-duration statistics also differed from the kernel's (K1) and are now the kernel's.

**Process change, after finding P1.** This section is committed alone, before any run is launched. The results commit follows and carries the JSON. A referee can check the two commit times against a run's duration.

**Runs.**
- 12a: roster `phase_v1`, seeds 12001 to 12020, policies `recommended`, `aimd_capped_drift`, `twoway_h8`, `twoway_h16`.
- 12b: rosters `headroom75_v1` and `headroom60_v1` (the v1 agents under 75 ms and 60 ms deadlines), seeds 12001 to 12020, policies `recommended`, `aimd_capped_drift`, `twoway_h16`.
- 12c: rosters `v1`, `pulse_v1`, `holdout_v1`, the same seeds as rounds 9 to 11 (8001 to 8020), policies `recommended`, `aimd`, `aimd_capped_drift`, `twoway_h16`, under the kernel-exact statistics. Every number is reported beside its round 9 to 11 value.

All outcomes now carry `legit_kills`, `net_burn_ns` (hung time saved minus what the newly killed legitimate tasks had burned) and `net_rerun_ns` (the same minus the work a rerun repeats). `experiments/score_rounds.py` computes every table below from the JSON and is committed with this section.

**Predictions.**

| # | Prediction | Threshold |
|---|---|---|
| Q1 (B1) | The ratchet does not catch a patient pulser. On `pulse-8-ph7` and `pulse-8-ph11` it saves under 5% and tightens in fewer than half the seeds; on `pulse-16-ph15` under 3%. On `pulse-8-ph0` it saves what pulse_v1 gave (16 ± 3%). | all four cells |
| Q2 (B2) | `twoway_h16` saves at least 14% on the period-8 pulser at every phase and at least 8% on `pulse-16-ph15`, and beats the ratchet by at least 8 points on the ph7, ph11 and ph15 cells. | all cells |
| Q3 (B2) | `twoway_h16` pooled should-not cost on `phase_v1` is within 0.5 points of the ratchet's; on v1 (12c) within 0.5 points with the slow-from-start pair included and within 0.2 without it. | both |
| Q4 (T2) | In net time with burn (`net_burn_ns`), the period-8 and period-16 pulsers and `attacker-lowslow` are net negative under every policy that tightens them, in at least 15 of 20 seeds each; `runaway-steady` and `runaway-mild` are net positive under every policy in at least 18 of 20. | all |
| Q5 (T1) | At 60 ms, the ratchet tightens should-not agents in more than half of agent-seeds and its pooled should-not cost exceeds 6 points; at 75 ms the cost is between the 100 ms value and the 60 ms value. | both |
| Q6 (T1, T2) | At 60 ms the roster's total net time (burn) is negative under every policy, and `runaway-steady` itself is net negative under the ratchet in at least 15 of 20 seeds. | both |
| Q7 (T1) | The ratchet and `twoway_h16` stay within 1.0 point of each other in pooled should-not cost at 75 ms and at 60 ms: headroom hurts both alike. | both |
| Q8 (K1) | Under the kernel-exact statistics, on seeds 8001 to 8020: v1 pooled should-not cost for the ratchet within 0.1 of 1.99; sustained saving within 0.5 point of 25.6%; `pulse_v1` `attacker-pulse-8` within 2 points of 16%; the number of restore requests and operator decisions on v1 within 10% of round 11's 216. | all four |
| Q9 (M4) | Of the ratchet's operator decisions on v1, at least 50 fall on should-not agents and at least 35 of those on the slow-from-start pair; `attacker-inflate` alone accounts for more than half of the attacker decisions. | all three |
| Q10 (B2, the headline) | Taking 12a, 12b and 12c together there is no cell in which the ratchet beats `twoway_h16` by 8 or more points of saving, and no honest agent on which `twoway_h16` costs 1.0 point or more above the ratchet except `healthy-tail`. If this holds, the "direction matters" claim is withdrawn, not softened. | both |

**What would falsify the red team.** Q1 or Q2 failing means the cold-start explanation is wrong or hysteresis does not travel to fresh seeds; Q5 failing means the headroom dependence was seed noise; Q8 failing means the committed rounds must be regenerated before they are cited again.

### Round 12, results (2026-10-14; `..._round12a/b/c.json`; predictions committed at 19:09:23 UTC in 1e247c7, runs launched after, scored by `experiments/score_rounds.py`)

**12a, phase-shifted pulsers** (saved% / seeds tightened; net time with burn, ms per seed, and seeds negative):

| agent | ratchet | capped + drift | two-way, 16-slice hysteresis |
|---|---|---|---|
| pulse-8, first pulse slice 0 | 17.0% / 20 (-1069, 19 neg) | 0.1% / 20 | 18.5% / 20 (-1455, 20 neg) |
| pulse-8, slice 3 | 7.5% / 16 | 0.8% / 20 | 18.2% / 20 |
| pulse-8, slice 7 | **2.5% / 6** | 0.0% / 20 | **18.7% / 20** |
| pulse-8, "slice 11" (see below) | 8.8% / 19 | 0.7% / 20 | 19.2% / 20 |
| pulse-16, slice 15 | **0.0% / 0** | 0.0% / 20 | **10.8% / 20** |
| pulse-4, slice 3 (steady 1.25%, above the rule) | 18.0% / 20 (+112, 10 neg) | 14.4% / 20 | 22.6% / 20 (+39, 12 neg) |
| pulse-8 width 2, slice 7 | 22.2% / 20 (+99, 9 neg) | 12.9% / 20 | 23.1% / 20 (+51, 11 neg) |
| runaway-steady | 26.0% (+8204, 0 neg) | 25.5% | 26.0% (+8210, 0 neg) |
| pooled should-not cost | 0.26 | 0.49 | 0.60 (healthy-tail 1.38 against 0.40) |
| operator decisions | 92 | 0 | 0 |

The "slice 11" agent is mis-specified: with period 8, a phase of 11 wraps to 3 (Python's modulo), so it pulses in slice 3 as well, where the window is still filling. It is a second phase-3 agent under different seeds and reads like one.

| # | Prediction | Result | Held? |
|---|---|---|---|
| Q1 | ratchet under 5% and under half the seeds on ph7 and "ph11"; under 3% on ph15; 16 ± 3% on ph0 | ph7 2.5% / 6 of 20; ph15 0.0% / 0 of 20; ph0 17.0%; **"ph11" 8.8% / 19 of 20** (the wrapped agent) | 3 of 4 as written; the miss is the mis-specified agent |
| Q2 | two-way with hysteresis at least 14% on period 8 at every phase, at least 8% on ph15, at least 8 points over the ratchet on ph7, "ph11", ph15 | 18.2 to 19.2% at every phase; 10.8% on ph15; margins 16.2, 10.4, 10.8 | **Yes, every cell** |
| Q3 | pooled should-not cost within 0.5 of the ratchet on phase_v1; on v1 within 0.5 with the slow-from-start pair and 0.2 without | 0.60 against 0.26; v1 2.07 against 2.00, and 0.17 against 0.08 | Yes |

**12b, deadline headroom** (v1 agents; pooled should-not cost, with and without the slow-from-start pair; should-not agent-seeds tightened of 320; net time with burn as a share of all static time):

| deadline | ratchet | capped + drift | two-way, hysteresis |
|---|---|---|---|
| 100 ms (12c) | 2.00 / 0.08; 67 of 320; -2.8% | 1.99 / 0.15; 111; -2.8% | 2.07 / 0.17; 71; -2.9% |
| 75 ms | 3.36 / 0.76; 109 of 320; -3.6% | 5.55 / 3.36; 231; -5.6% | 5.95 / 3.72; 232; -5.9% |
| 60 ms | 8.24 / 6.15; 220 of 320; -6.2% | 8.32 / 6.35; 221; -6.4% | 8.40 / 6.34; 221; -6.4% |

At 75 ms the ratchet holds the honest regressions and drifts (regressed-0 1.49 points, drift-0 1.36) where the two-way policies do not (10.45 and 10.59): the ratchet's drift machinery fits the slowdown under deadline truncation (`_slowdown`), the transplanted hold compares a raw, truncated median. It pays 131 operator decisions on should-not agents for that. At 60 ms every policy tightens every regression, drift, slow-from-start and tail agent in every seed, and `runaway-steady` itself is net negative in time (20 of 20 seeds under the ratchet: 4.6 points of its own completing tasks killed for a 26% cut in hung time).

| # | Prediction | Result | Held? |
|---|---|---|---|
| Q5 | at 60 ms more than half the should-not agent-seeds tightened and pooled cost over 6; 75 ms in between | 220 of 320, 8.24; 75 ms 3.36 | Yes |
| Q6 | at 60 ms total net time negative under every policy; runaway-steady net negative under the ratchet in 15 of 20 | -6.2%, -6.4%, -6.4%; 20 of 20 | Yes |
| Q7 | ratchet and two-way within 1.0 point at both headrooms | 60 ms: 0.16 apart; **75 ms: 2.59 apart, in the ratchet's favour** | Half: the ratchet's truncation-aware drift fit is a real advantage at 75 ms, and it is a feature of the bridge, not of the direction |

**12c, the decisive rosters under the kernel-exact statistics** (seeds 8001 to 8020, the same as rounds 9 to 11):

| number | rounds 9 to 11 | round 12c | moved? |
|---|---|---|---|
| v1 pooled should-not cost, ratchet | 1.99 | 2.00 | no |
| v1 sustained saved, ratchet | 25.6% | 25.6% | no |
| v1 operator decisions, ratchet | 216 (156 attackers, 60 should-not) | 216 (154, 62) | two decisions moved between groups |
| v1 pooled cost, plain AIMD | 17.75 | 17.75 | no |
| pulse_v1 attacker-pulse-8, ratchet / capped + drift | 16% / 0% | 16.5% / 0.0% | no |
| holdout inflate-first, ratchet (mild / strong / ramp) | 19 / 21 / 16% | 19.1 / 20.6 / 16.2% | no |

| # | Prediction | Result | Held? |
|---|---|---|---|
| Q8 | every headline number within its stated tolerance | all within | Yes: the K1 divergence does not move the multi-seed headlines, only single-trace verdicts |
| Q9 | at least 50 should-not decisions, at least 35 on slow-from-start, inflate more than half the attacker decisions | 62, 40, 86 of 154 | Yes |
| Q4 | period-8 and period-16 pulsers and low-and-slow net negative (burn) in at least 15 of 20 under every policy that tightens them; sustained runaways net positive in at least 18 of 20 | narrow pulsers: 19, 20 (ratchet), 20, 20 (two-way), 17, 12 (capped + drift, which barely tightens them); **width-2 pulser about even (9 to 11 of 20)**; **low-and-slow under the ratchet 10 and 11 of 20, under the two-way 18 and 19**; runaways 20 of 20 everywhere. Under the rerun accounting every pulser and the low-and-slow attacker are negative in nearly every seed under every policy | Half: a one-slice pulser is a net loss to tighten; a two-slice pulser and the low-and-slow attacker sit at the break-even |
| Q10 | no cell where the ratchet beats the two-way by 8 or more points; no honest agent 1.0 point worse under the two-way except healthy-tail | **holdout inflate-first attackers: 19.1 / 20.6 / 16.2% against 0 / 0 / 7.3%**; **75 ms regressions and drifts 5 to 9 points worse under the two-way** | **No, on both parts.** Both are features the two-way policy was not given: the excess release (round 10c) and the truncation-aware slowdown fit. Neither is the direction. |

**What round 12 settles.**
1. The one-way direction's measured benefit in rounds 9 to 11 was the cold start (Q1, Q2). A patient pulser that waits for the rate window to fill is not caught by the ratchet at all (2.5% and 0%), and a two-way limiter with 16 slices of hysteresis catches it at every phase (18 to 19%, 11%) with no human decisions. **The "direction matters" claim is withdrawn.** What remains to the direction is the auditability of every change and the fact that no workload can move a limit up; neither is measured by these replays.
2. The ratchet's bridge still beats the transplanted two-way on two things, and both are interpretation, not direction: the excess release against inflate-first attackers (Q10) and the truncation-aware drift fit at 75 ms headroom (Q7). A two-way limiter could carry both; that is the obvious next experiment and it is not run here.
3. The honest-agent numbers are a headroom result (Q5, Q6). At three times the slowest honest median, every policy tightens every honest agent with a tail and the whole loop is a net loss of 6% of all task time; at 100 ms the loop is a net loss of 2.8% under every policy, carried by the inflate attacker's and the slow-from-start agents' killed tasks. Only sustained runaways are a net win in time.
4. The kernel-exact statistics do not move the multi-seed headlines (Q8); they moved single-trace verdicts and one restore request in the kernel comparison, which is where K1 was found.
5. Strict count: Q2, Q3, Q5, Q6, Q8, Q9 held; Q1, Q4, Q7 half; Q10 failed. The two misses that matter (Q7 at 75 ms, Q10) both favour the bridge's interpretation rules over the direction, and both say the decomposition was incomplete rather than wrong.

### Round 13, pre-registered before any run (2026-10-14): the same bridge, three directions

**Question.** Round 12 left two cells where the ratchet beat the plain two-way limiter: the inflate-first attackers (the excess release) and the 75 ms regressions (the truncation-aware slowdown fit). Both are interpretation rules the two-way was not given. Round 12 also showed the plain two-way catching the phase-7 pulser, which the ratchet's rate rule misses; that comes from its per-slice trigger, not its direction. To separate direction from everything else, this round holds the interpretation layer constant: `twoway_bridge_h8` and `twoway_bridge_h16` are the ratchet's own `TrapBridge` with the recommended policy, driving a limit book instead of the governor (`BridgeTwoWay` in `experiments/stack_trace_sim.py`). Same rate rule over the filling window, same drift hold and slowdown fit, same excess release, same recovery clearing, same expected-load hook, same one-step-per-five-traps. Differences: the cap is a floor at 72.9% rather than three steps and a hold; nobody is asked anything; after 8 or 16 consecutive calm slices the limit comes back up by 2% of the original per slice. Any difference between `recommended` and these two is the direction alone.

**Runs** (launched after this commit): rosters `v1`, `pulse_v1`, `holdout_v1` on seeds 8001 to 8020 and `phase_v1`, `headroom75_v1` on seeds 12001 to 12020; policies `recommended`, `twoway_bridge_h8`, `twoway_bridge_h16`. Scored with `experiments/score_rounds.py` beside round 12's `twoway_h16` columns.

**Predictions.**

| # | Prediction | Threshold |
|---|---|---|
| X1 | Interpretation travels: on every should-tighten agent of v1, pulse_v1, holdout_v1 and phase_v1, both bridge two-ways save within 2 points of the ratchet, except the two cells named in X2 and the episodic runaway. | all cells but those |
| X2 | The one direction cell, by the formula `(period - 1 - K) x increase >= 0.10`: on `attacker-pulse-16` and `pulse-16-ph15`, `twoway_bridge_h8` (7 x 0.02 = 0.14) saves at least 5 points less than the ratchet, and `twoway_bridge_h16` (negative) within 2 points of it. | both agents, both policies |
| X3 | Recovery replaces the human restore at no measured cost: pooled should-not cost under `twoway_bridge_h8` is at or below the ratchet's on v1 and on headroom75_v1, and within 1.0 point of it on headroom75_v1 (the plain two-way was 2.6 points worse there). | all three |
| X4 | The inflate cell closes: on `ho-inflate-mild`, `-strong`, `-ramp` both bridge two-ways save within 3 points of the ratchet (19.1 / 20.6 / 16.2%), where the plain two-way saved 0 / 0 / 7.3%. | all three agents |
| X5 | Both bridge two-ways ask zero operator decisions on every roster; the ratchet asks 216 on v1 and more than 100 on headroom75_v1. | all |
| X6 | Headline: across every cell of the five rosters, the only places the ratchet beats a bridge two-way by 5 or more points of saving are the period-16 pulsers under `h8`. If so, the decomposition is complete: everything else the ratchet does is cap and interpretation, the direction's measured value is exactly the formula's cell, and hysteresis of `K >= period - 1 - 0.10 / increase` removes it at no honest cost on these traces. | the headline |

**What would falsify this.** X1 or X4 failing means the limit book and the governor differ in something other than direction (a bug in `BridgeTwoWay`, to be found before anything is concluded). X2 failing in the h8 cell means the formula is wrong even with the interpretation layer held fixed. X3 failing means automatic recovery costs honest agents something the restore request does not.

### Round 13, results (2026-10-14; `..._round13a.json`, `..._round13b.json`; predictions committed at 23:44:51 UTC in ffb7cb1, runs launched after)

With the interpretation layer held fixed, the three directions are **identical on every cell of all five rosters except two**: the saving, the legitimate cost, the seeds tightened, the net time, all equal to the printed precision, with the ratchet asking 216 decisions on v1 and 269 on headroom75_v1 and the two-way bridges asking none.

| cell | ratchet | bridge two-way, K = 8 | bridge two-way, K = 16 |
|---|---|---|---|
| `attacker-pulse-16` (pulse_v1, first pulse in slice 0), saved | 10.4% | **7.4%** | 10.4% |
| `regressed-0` (phase_v1), cost | 0.38 | **0.23** | 0.38 |
| everything else (v1, pulse_v1, holdout_v1, phase_v1, headroom75_v1) | = | = | = |
| `ho-inflate-mild / -strong / -ramp`, saved (plain two-way in round 12: 0 / 0 / 7.3%) | 19.1 / 20.6 / 16.2% | same | same |
| headroom75_v1 pooled should-not cost (plain two-way in round 12: 5.95) | 3.36 | 3.36 | 3.36 |
| operator decisions, v1 / headroom75_v1 | 216 / 269 | 0 / 0 | 0 / 0 |

| # | Prediction | Result | Held? |
|---|---|---|---|
| X1 | interpretation travels: within 2 points everywhere but the named cells | identical everywhere but the named cells | Yes |
| X2 | pulse-16 under K = 8 at least 5 points under the ratchet; under K = 16 within 2 | **3.0 points under** (7.4 against 10.4), sign right, size short; K = 16 identical. The phase-15 agent is caught by nobody (0 / 0 / 0), so that cell is empty | Half |
| X3 | recovery at or below the ratchet's honest cost on v1 and headroom75_v1, within 1.0 there | equal (2.00, 3.36); on phase_v1 lower (0.21 against 0.26) | Yes, with equality |
| X4 | the inflate cell closes | identical | Yes |
| X5 | zero decisions; ratchet 216 and over 100 | 0; 216 and 269 | Yes |
| X6 | the only cells the ratchet wins by 5 or more are period-16 pulsers under K = 8 | the ratchet wins no cell by 5 or more; its largest edge is 3.0 points on one agent under K = 8 and nothing under K = 16 | Yes, more strongly than written |

**Why they are identical.** The two-way bridge raises a limit only after K consecutive calm slices, and on these traces the agents that get tightened are the ones that keep trapping: the slow-from-start agents, the tail agents at 75 ms, the inflate attackers, the sustained runaways. The drift hold already keeps the transient ones (regressions, drifts) from being tightened. So the recovery rule almost never fires, and when it does (the period-16 pulser, with 15 calm slices between pulses; a regression that was tightened before its hold engaged) it changes one number by 3 points or 0.15 points. Within a 32-slice run, the direction is nearly moot. The breaker's three steps and the floor at 72.9% are the same depth, so the cap does not separate them either.

**What rounds 12 and 13 settle together.** Everything the ratchet's bridge does that protects an honest agent or catches an attacker is cap and interpretation, and a two-way limiter carrying the same rules reproduces it exactly with no human in the loop. The one-way direction's measured value on these traces is 3 points of saving on one patient pulser against a limiter with 8 slices of hysteresis, and nothing against one with 16. What the direction still buys is not in these replays: every change attributable to a signal or a named person, no path by which a workload moves a limit up, and a held tightening that waits for a person rather than decaying on its own. Those are properties of the audit and the authority model, and the paper should claim them as such, not as measured protection.

### Note on the pre-registration record (2026-10-15)

The branch carrying rounds 10 to 13 was rebased onto main before it was merged (PR #7), which rewrote the committer timestamps of every commit on it to the rebase time. The ordering evidence for rounds 12 and 13 (predictions commit, then results commit, separated by more than a run's duration) therefore lives in the original commits, which are preserved on the remote as the branch `prereg-record` (head 08f6fe2, whose ancestry holds 1e247c7 at 19:09:23 UTC, 58ea258 at 19:14:19 UTC, ffb7cb1 at 23:44:51 UTC and 08f6fe2 at 23:48:38 UTC, committer dates). Tags with the same names exist locally but could not be pushed through this session's proxy. The rebased copies on main carry the same author dates and content. From round 14 on, the branch carrying a pre-registration is merged without rebasing.

Correction, same date: the round 12 headroom table's should-not agent-seeds tightened at 100 ms read 47 / 49 / 46; the JSON gives 67 / 111 / 71 (the paper's `data/numbers.json` reproduces the latter). Corrected above.


### Round 14, pre-registered before any run (2026-10-15): the one-way limiter with the per-slice trigger

**Question.** The paper's red team showed that the phase-7 pulser catch in round 12 (18.7% under the hysteresis two-way, 2.5% under the ratchet) belongs to the two-way baseline's per-slice decrease trigger (3 traps in a 250-task slice), not to hysteresis, and noted that the control isolating the trigger from the direction was never run. This round runs it: `recommended_slice` is the ratchet's own bridge, governor, cap and human gate with the rate rule's window shortened to 250 tasks and its minimum to 3 traps. Everything else is `recommended`.

**Runs** (launched after this commit): rosters `phase_v1` (seeds 12001 to 12020) and `v1` (seeds 8001 to 8020); policies `recommended`, `recommended_slice`, `twoway_h16`.

| # | Prediction | Threshold |
|---|---|---|
| Y1 | The one-way limiter with the per-slice trigger catches the phase-7 period-8 pulser: saving at least 14%, tightened in at least 18 of 20 seeds, within 5 points of `twoway_h16`'s 18.7%. | all three |
| Y2 | It pays the trigger's price on the natural-tail program: `healthy-tail` tightened in at least 18 of 20 seeds on v1 and costing at least 1.0 point (the ratchet: 7 of 20 and 0.28; `twoway_h16`: 20 of 20 and 1.33). | both |
| Y3 | Its pooled should-not cost on v1 is within 0.3 of `twoway_h16`'s 2.07, and its operator decisions on v1 exceed the ratchet's 216 (more tightenings, more holds). | both |

If Y1 holds, the catch is the trigger and the direction is cleared of the last cell where it appeared to matter. If Y1 fails, the direction contributes to the catch and section 5.2 of the paper must say so.

### Round 14, results (2026-10-15; `..._round14a.json`, `..._round14b.json`; predictions committed at 13:56:11 UTC in f8e7392, runs launched after, branch merged without rebasing)

| cell | ratchet | ratchet with per-slice trigger | two-way, cap + drift + 16-slice hysteresis |
|---|---|---|---|
| pulse-8, first pulse slice 7: saved / seeds tightened | 2.5% / 6 of 20 | **19.8% / 20 of 20** | 18.7% / 20 of 20 |
| pulse-8, first pulse slice 0 | 17.0% | 19.7% | 18.5% |
| pulse-16, first pulse slice 15 | 0.0% / 0 of 20 | **13.8%** / 20 of 20 | 10.8% / 20 of 20 |
| healthy-tail (v1): cost / seeds tightened | 0.28 / 7 of 20 | 1.33 / 20 of 20 | 1.33 / 20 of 20 |
| v1 pooled should-not cost | 2.00 | 2.12 | 2.07 |
| v1 operator decisions | 216 | 254 | 0 |

| # | Prediction | Result | Held? |
|---|---|---|---|
| Y1 | at least 14%, at least 18 of 20 seeds, within 5 points of the two-way | 19.8%, 20 of 20, 1.1 points | Yes |
| Y2 | healthy-tail at least 18 of 20 and at least 1.0 point | 20 of 20, 1.33 | Yes |
| Y3 | pooled cost within 0.3 of 2.07; decisions above 216 | 2.12; 254 | Yes |

**What it settles.** The phase-7 catch of round 12 was the trigger, not the direction: give the one-way limiter the two-way baseline's per-slice trigger and it catches the patient pulser at the same rate and pays the same tail-program price. The direction is cleared of the last cell where it appeared to matter in round 12. One residue stays, and it is the same one round 13 found: on the period-16 pulser the one-way limiter keeps 3 points more saving (13.8% against 10.8%) because the two-way gives part of the cut back between pulses. That is the direction's measured value on these traces, stated as a number: about 3 points of hung time on a pulser with 15 calm slices between pulses, bought with 254 operator decisions per 500 program-runs instead of none.


### Round 15, pre-registered before any run (2026-11-18): the two experiments a referee still demands

**Why.** The 2026-10-15 handoff listed two experiments the paper's section 7 says a referee would still ask for: runs longer than 32 slices with a sweep of the calm share, and net time as a curve against the share of misbehaving agents. Every earlier round replayed 32 slices (8,000 tasks per agent) and every net-time statement was made on one roster. Nothing from either experiment has been looked at: the two scripts were smoke-tested on one throwaway seed (99001) for crashes only, and that output was deleted unread.

**15a, run length x calm share** (`experiments/stack_trace_round15.py a`; roster `calm_v1`; seeds 13001 to 13020; 32, 64 and 128 slices of 250 tasks; policies `recommended`, `recommended_slice`, `twoway_h16`). Seven misbehavers differ in calm share, the share of slices with no hangs: 0% (steady runaway), 25%, 50%, 75%, 87.5%, 93.75% and 96.9% (periods 4 with width 3, 2, 4, 8, 16, 32; hang rate 5% in active slices; first pulse after the rate window has filled). Three honest programs ride along: `honest-healthy`, `honest-tail` (0.6% natural tail) and `honest-regressed` (3x slower from task 4000, for good). Positions of events (the regression at task 4000) do not scale with run length; the roster is the same at every length.

| # | Prediction | Threshold |
|---|---|---|
| A1 | Saving does not dilute with length. `recommended_slice` on the steady runaway (calm 0%): saved share at 128 slices is within 3 points of its value at 32 slices and at least 24%. | both |
| A2 | The one-way limiter keeps catching patient pulsers as the run lengthens. `recommended_slice` tightens every pulser (calm 25% to 96.9%) in at least 18 of 20 seeds at every length, and its saved share rises with length: at 128 slices it is at least its 32-slice value for each pulser, and at least 20% for every pulser with calm share up to 87.5%. | all |
| A3 | The direction's value is bounded and appears only past the recovery delay. The saved-share gap `recommended_slice` minus `twoway_h16` at 128 slices is at most 5 points for every pulser with period up to 16, and at least 6 points for the period-32 pulser (calm 96.9%), where 31 calm slices outlast the 16-slice hysteresis. | both |
| A4 | Honest cost saturates at the cap. `honest-tail` under `recommended_slice`: tightened in at least 19 of 20 seeds at 64 and at 128 slices, cost at 128 slices at least 1.2 points and within 0.4 of its 64-slice cost. Under `recommended` the tightened count at 128 slices is at least 14 of 20 (round 14: 7 of 20 at 32). | all |
| A5 | The drift hold erodes slowly. `honest-regressed` under `recommended_slice`: cost at 128 slices exceeds its 32-slice cost by at most 1.5 points. | one |
| A6 | The human price grows less than the run. `recommended_slice` operator decisions summed over the roster at 128 slices are at most 2.5 times those at 32 slices (the run is four times longer). | one |

**15b, net time against the misbehaving share** (`... round15.py b`; seeds 13001 to 13020; 32 slices; policies as above). A program book (`book_v1`, `book75_v1`, `book60_v1`: the same 12 programs at deadlines of 100, 75 and 60 ms) has six honest programs (healthy, healthy-tail, regressed, drifting, slow-from-start, scheduled batch) and six misbehaving ones (steady runaway, mild runaway, episode, low-and-slow, inflate-first attacker, period-8 pulser at phase 7). Because replays treat agents independently, a roster with misbehaving share m has expected net time `m x M + (1 - m) x H`, where M and H are the per-agent means over the two pools; the curve is drawn from that, and the breakeven share is `m* = -H / (M - H)` when H is negative and M positive. Net time is `net_burn_ns` (hung time saved minus what newly killed legitimate tasks had burned) and also `net_rerun_ns`; shares are of static time (all task time without governance). Direct rosters of 24 agents at 25%, 50% and 75% misbehaving (`mix25_v1`, `mix50_v1`, `mix75_v1`, 100 ms) check the mixing formula.

| # | Prediction | Threshold |
|---|---|---|
| B1 | At 100 ms under `recommended`, net time (burn) per agent is negative at 0% misbehaving and positive at 100%, with a breakeven share between 5% and 40%. | all |
| B2 | Headroom moves the breakeven up. Under `recommended` the breakeven at 75 ms is at least 15 points above that at 100 ms; at 60 ms net time is negative at every share from 0% to 100% (round 12: the steady runaway itself is net negative at 60 ms). | both |
| B3 | The per-slice trigger costs net time where the pool is mostly honest. At 100 ms `recommended_slice` has lower net time than `recommended` at 0% misbehaving, and its breakeven share is no lower than `recommended`'s minus 10 points. | both |
| B4 | The direction is invisible in net time. At 100 ms the `recommended_slice` and `twoway_h16` curves differ by at most 1 point of static time at every share. | one |
| B5 | The mixing formula is right. At 25%, 50% and 75% the direct mixed rosters' mean net time (burn) as a share of static time is within 1 point of the formula's value, for each policy. | one |

**Reading rules.** A prediction that misses is reported as missed. Any rule, metric or roster changed after a result is labelled as such, with the result that prompted it, as in rounds 4 to 11. The run uses fresh seeds (13001 to 13020) and rosters appended at the end of the generator, which leaves every earlier agent's seed unchanged.


### Round 15, results (2026-11-18; `experiments/stack_trace_round15a.json`, `..._round15b.json`, scored by `experiments/score_round15.py`; predictions committed in d1d9d27 at 21:20:38 local time, runs launched after, branch merged without rebasing)

**15a, run length x calm share** (saved share of hung time, % / seeds tightened of 20, at 32, 64 and 128 slices).

| calm share | `recommended_slice` | `twoway_h16` | `recommended` |
|---|---|---|---|
| 0% | 26.2 / 26.7 / 26.9 | 26.0 / 26.6 / 26.8 | 26.0 / 26.6 / 26.8 |
| 50% | 25.3 / 26.3 / 26.7 | 25.0 / 26.0 / 26.6 | 24.7 / 26.0 / 26.5 |
| 75% | 23.8 / 25.3 / 26.2 | 23.0 / 25.1 / 26.0 | 18.5 / 22.9 / 24.8 |
| 87.5% | 19.7 / 23.5 / 25.4 | 18.5 / 22.8 / 25.0 | 1.2 / 3.6 / 10.8 (4, 7, 16 of 20 tightened) |
| 93.75% | 12.2 / 20.3 / 23.8 | 11.1 / 18.6 / 23.1 | 0.9 / 0.6 / 4.0 (5, 5, 11 of 20) |
| 96.9% | 0.8 / 14.1 / 21.1 | 0.0 / 10.8 / 19.9 | 0.0 / 0.7 / 3.2 (2, 4, 11 of 20) |

Honest programs, cost in points / seeds tightened:

| program | `recommended` | `recommended_slice` | `twoway_h16` |
|---|---|---|---|
| honest-tail | 0.31/7, 0.84/15, 0.80/16 | 1.37/20, 1.60/20, 1.65/20 | 1.37/20, 1.59/20, 1.65/20 |
| honest-regressed (3x from task 4000, for good) | 0.09/3, 1.01/7, 2.25/11 | 0.09/3, 1.01/7, 2.98/11 | 0.00/0, 0.00/0, 0.23/1 |

Operator decisions summed over the roster and 20 seeds (32, 64, 128 slices): `recommended_slice` 156, 170, 177; `recommended` 88, 108, 126; `twoway_h16` none.

| # | Prediction | Result | Held? |
|---|---|---|---|
| A1 | steady runaway within 3 points of its 32-slice value and at least 24% | 26.2% to 26.9% | Yes |
| A2 | every pulser tightened in at least 18 of 20 at every length; saving rises with length; at least 20% to calm 87.5% at 128 slices | 20 of 20 everywhere; every pulser rises; 25.4% at 87.5% | Yes |
| A3 | gap (slice minus twoway) at most 5 points up to period 16 and at least 6 points at period 32 | gaps at 128 slices 0.1, 0.1, 0.2, 0.3, 0.7, 1.2 (period 32: **1.2**, not 6); the largest gap in any cell is 3.3 (period 32 at 64 slices) | **No** (first half held) |
| A4 | honest-tail tightened at least 19 of 20 at 64 and 128 slices, cost at 128 at least 1.2 and within 0.4 of 64; `recommended` at least 14 of 20 at 128 | 20 and 20; 1.65 against 1.60; 16 of 20 | Yes |
| A5 | honest-regressed cost at 128 slices at most 1.5 points above its 32-slice cost | **+2.89** under `recommended_slice` (0.09 to 2.98); +2.16 under `recommended`; the two-way: +0.23 | **No** |
| A6 | decisions at 128 slices at most 2.5 times those at 32 slices | 177 against 156: ratio 1.13 | Yes |

**What 15a says.**
1. Run length does not dilute the saving: it rises toward the cap's arithmetic (26.9% against 27.1%) as the first pulses stop being a large share of the run, and patient pulsers are caught at every length by the per-slice trigger. A longer run helps the one-way limiter; the plain rate rule (`recommended`) still misses the sparse pulsers (4% saved at calm 93.75% even at 128 slices).
2. The direction is worth little at any length. The widest gap between the one-way and two-way limiters anywhere in the sweep is 3.3 points of hung time (period 32, 64 slices), shrinking to 1.2 at 128 slices as the two-way limiter's recovery gets re-spent on each pulse. A3's second half was wrong because the two-way limiter that recovers fully between pulses is re-tightened by the next pulse's traps within a slice or two and loses little; the formula of round 10b (`(period - 1) x increase >= 0.10`) predicts that the budget comes back, not that the loss is large.
3. **The one-way limiter's honest-agent cost grows with run length, and the two-way limiter's does not.** `honest-regressed`, an honest 3x slowdown that never recovers, costs the ratchet 0.09 points at 32 slices, 1.01 at 64 and 2.98 at 128 (11 of 20 seeds tightened by 128), because the excess-rate release fires occasionally on the slow period's traps and the tightening is then permanent until a person restores it. The two-way limiter with the same cap and drift hold pays 0.23 (1 of 20). This is the first measured case where the one-way property costs honest agents more than it buys, and it grows with run length: on 32-slice replays it was invisible. It is the drift hold's erosion, not the direction itself, that creates the false tightenings; the direction is what makes each one stick. Decisions stay almost flat with length (156 to 177), so the human is not the one who fixes it in these runs: a person is asked once per tightened agent and the count saturates.

**15b, net time against the misbehaving share** (net time with burn, as a percentage of static time, from the pools at 100, 75 and 60 ms).

| deadline, policy | 0% | 25% | 50% | 75% | 100% |
|---|---|---|---|---|---|
| 100 ms `recommended` | -4.76 | -4.08 | -3.32 | -2.46 | -1.50 |
| 100 ms `recommended_slice` | -5.26 | -4.48 | -3.62 | -2.66 | -1.57 |
| 100 ms `twoway_h16` | -5.16 | -4.40 | -3.56 | -2.62 | -1.57 |
| 75 ms `recommended` | -5.21 | -4.95 | -4.67 | -4.35 | -4.00 |
| 75 ms `recommended_slice` | -7.95 | -7.15 | -6.27 | -5.30 | -4.21 |
| 60 ms `recommended` | -5.81 | -5.97 | -6.14 | -6.33 | -6.53 |
| 60 ms `recommended_slice` | -6.04 | -6.18 | -6.32 | -6.49 | -6.66 |

(`twoway_h16` at 75 and 60 ms is within 0.15 points of `recommended_slice` at every share.) Net time per agent-run at 100 ms under `recommended_slice`, in milliseconds, by program: healthy 0, healthy-tail -7,028 (20 of 20 seeds negative), regressed -395, drifting -3,538, slow-from-start -76,106 (20 of 20), batch 0; steady runaway +8,405 (0 of 20 negative), mild runaway +2,975, episode +1,121, low-and-slow -560 (19 of 20), inflate-first attacker -31,520 (20 of 20), period-8 pulser -1,125 (20 of 20). The honest pool averages -14.5 s, the misbehaving pool -3.5 s.

| # | Prediction | Result | Held? |
|---|---|---|---|
| B1 | at 100 ms under `recommended`, net negative at 0% and positive at 100%, breakeven between 5% and 40% | negative at both ends (-4.76% and -1.50%); **no breakeven** | **No** |
| B2 | breakeven at 75 ms at least 15 points above that at 100 ms; negative everywhere at 60 ms | no breakeven at either deadline; 60 ms negative at every share | Half (the 60 ms half) |
| B3 | `recommended_slice` lower net than `recommended` at 0%; its breakeven within 10 points of `recommended`'s | lower at 0% (-5.26 against -4.76); no breakeven to compare | No (the second half is undefined) |
| B4 | `recommended_slice` and `twoway_h16` curves within 1 point of static time at every share | largest difference 0.10 points | Yes |
| B5 | direct mixed rosters within 1 point of the formula at 25%, 50%, 75% for each policy | largest difference 0.02 points (nine cells) | Yes |

**What 15b says.** The sign of net time is set by the program mix, not by the misbehaving share, and on this pool it is negative at every share, including a roster made only of misbehaving agents (-1.5% to -6.7% of static time). Three of the six misbehaving programs (the inflate-first attacker, low-and-slow, the patient pulser) are net negative in 19 or 20 of 20 seeds, and the inflate-first attacker alone (-31.5 s) outweighs all three sustained runaways together (+12.5 s): hung time saved is tiny against the legitimate tasks the tightened deadline kills. Honest agents cost more still, driven by one program: the legitimately slow agent costs -76 s per run, 5 times the whole honest pool's mean. I predicted a breakeven at 5% to 40%; that came from reading round 12's single-roster table as if the runaways dominated the misbehaving side. They do not. The mixing formula itself is exact (B5, to 0.02 points), so any roster's net time can be read off the per-program table above, and the direction is invisible in net time (B4).

**Post-hoc readings, labelled as such (not predictions; the pools were chosen after seeing the table above).** The sign flips only when the misbehaving pool is the three sustained hangers and honest slow agents are rare: with the misbehaving pool = {steady runaway, mild runaway, episode} and the honest pool minus slow-from-start, breakeven is at 12% misbehaving under `recommended` and 31% to 35% under the per-slice trigger and the two-way limiter; with the slow-from-start agent included it is 77% to 78% under all three. With all six misbehaving programs there is no breakeven under any honest pool tried. The number the paper should quote is therefore not a share but a per-program table, and the claim it supports is narrow: the governor earns net time against sustained hangs, and loses it to every other program in the book.

**Consequences for the paper (section 7).**
- The first referee experiment (longer runs, calm-share sweep) strengthens the saving and the decomposition (the direction is worth at most 3.3 points anywhere) and weakens the honest-agent claim: the drift hold's protection erodes with run length, 0.09 to 2.98 points on an honest regression, and only the two-way limiter recovers from it. Section 6 should not say honest regressions are held; it should say they are held for about 32 slices.
- The second (net time against share) removes the idea of a breakeven share on this book: net time is negative at every share. State the per-program table instead, and state that the mix formula was checked against direct rosters to 0.02 points.
- Limits of both: all durations are simulated; the honest pool contains one agent (slow-from-start) that dominates it; run length changed but not the positions of events inside the trace (the regression stays at task 4000); 20 seeds per cell, no intervals computed (the per-seed values are in the JSON files).

## Round 16, pre-registered: real coding-agent latencies

Written and committed before any replay of this data. Only the schema, the row counts and the pooled latency quantiles of the
source were looked at to fix the protocol; no governed or static outcome has been computed.

**Source.** TraceLab public coding-agent trace (uw-syfi/TraceLab, release v0.0.1, CC BY 4.0, sha256 9d265eae...da0b4e6b): 432,510 tool
calls from 43 pseudonymous developers' real Claude and Codex sessions. Pooled latency: median 0.2 s, p90 8 s, p99 180 s.

**Protocol.** One agent per user; a task is a tool call; duration is its measured latency; every call is treated as legitimate because
the data has no runaway label; tokens and memory are held at nominal so only the deadline can bind. Populations: long (users with
at least 8000 calls, first 8000, 9 agents) and wide (at least 2000 calls, first 2000, 23 agents). Deadlines 10 s, 60 s, 180 s.
Policies: recommended, recommended_slice, twoway_h16. Runner: `experiments/real_trace_round16.py`.

**Certificate under test.** For an agent with deadline D and the cap of three 10% steps, honest cost in points cannot exceed
100 x (share of its calls with latency in (0.729 D, D]). Checked on simulated rosters (1,344 honest agent-runs, 0 violations,
closest slack 0.012 points; scratch test, not committed).

| # | Prediction |
|---|---|
| R1 | zero violations of the certificate in every cell (agent x deadline x policy x population). One violation falsifies it as stated. |
| R2 | share of agents tightened by `recommended`: at least 80% at 10 s; between 20% and 60% at 180 s |
| R3 | mean honest cost under `recommended`: at least 2.0 points at 10 s; below 1.0 point at 180 s |
| R4 | `twoway_h16` costs less than `recommended_slice` in at least 75% of cells where `recommended_slice` costs more than 0.5 points |
| R5 | Spearman correlation between the bound and measured cost across cells with a bound above 0.5 points is at least 0.8 |

**Known limits, stated now.** Tool latency can include time a developer spent approving a prompt, so some long calls are human waiting,
not tool work. There is no ground truth for runaways, so this round measures honest cost and tightening behavior only, not
saving or net time. The tool is a coding assistant's tool calls, not the kernel's hang model.

## Round 16, results: real coding-agent latencies

Run once on the committed protocol (`python experiments/real_trace_round16.py`, then `python experiments/score_round16.py`); 288 cells
(32 agents across both populations x 3 deadlines x 3 policies). Raw cells: `experiments/real_trace_round16.json`.

| # | Prediction | Result | Held? |
|---|---|---|---|
| R1 | zero certificate violations | **0 of 288**; in several cells honest cost equals the bound exactly (for example 0.275 against 0.275); no limit went below 72.9% of its start | Yes |
| R2 | tightened by `recommended`: at least 80% at 10 s; 20% to 60% at 180 s | 87.5% at 10 s; 53.1% at 180 s | Yes |
| R3 | mean cost under `recommended`: at least 2.0 points at 10 s; below 1.0 at 180 s | 1.26 at 10 s; 0.21 at 180 s | Half (the 180 s half) |
| R4 | `twoway_h16` cheaper than `recommended_slice` in at least 75% of cells where the latter costs over 0.5 points | cheaper in 8 of 31 (25.8%); the two policies are within 0.05 points on average | No |
| R5 | Spearman between bound and cost at least 0.8 | 0.924 over 120 cells | Yes |

**What round 16 says.**
- The certificate held on real durations, not only simulated ones, and it is nearly exact: where an agent is tightened to the cap, its honest cost is almost all of the bound.
- With no runaway in the data at all, `recommended` tightened 87.5% of real agents at 10 s and 53.1% at 180 s. Real agents have natural tails above the 1% rate rule, so the limiter cries wolf on real traffic, as the trace rounds found in simulation. The certificate is what keeps this affordable: the cost is capped at the share of calls in the top 27.1% of the deadline (mean 1.26 points at 10 s, 0.21 at 180 s, worst agent 7.95).
- The two-way limiter did not win on real durations (R4 missed). On simulated rosters it paid far less than the one-way limiter at long runs; here the policies are nearly the same, so that advantage depends on the simulated honest-agent shapes.
- Mean static failure is 9.26% at 10 s and 1.47% at 180 s, so the deadline choice dominates the limiter's effect at the short deadline.

**Limits.** No runaway labels, so nothing here measures saving or net time. Tool latency can include time spent waiting for a developer to approve a prompt. These are a coding assistant's tool calls, not the kernel's hang model. Deadlines were chosen as round numbers from pooled quantiles, not tuned.

**Consequence for the claims.** The worst-case certificate is now supported on simulated and real durations. The claim that the two-way limiter is cheaper for honest agents is weakened. A labeled real-traffic test (shadow mode on live agents) is still missing.

## Round 17, pre-registered: the cost budget

Committed before any round 17 run (a one-seed smoke run was executed and deleted unread). Runner `experiments/real_trace_round17.py`,
scorer `experiments/score_round17.py`.

**The change.** `TrapPolicy(cost_budget=0.01)`: a deadline tightening never goes below the agent's own 99th percentile of completed
durations, estimated as the larger of its long-run and recent per-slice means; nothing tightens until 5 slices are known
(`docs/CERTIFICATE.md`). Policies compared: `recommended` against `recommended_budget`, and `recommended_slice` against
`recommended_slice_budget`.

**Honesty about the design.** The budget was designed after round 16's results, so round 16's 32 agents are diagnosis data (reported as
in-sample). The tests that count are hold-out: later, unread stretches of the same developers (H1: users with at least 4000 calls,
calls 2000 to 3999, 12 agents; H2: users with at least 16000 calls, calls 8000 to 15999, 6 agents; same people, not independent users),
and 20 fresh simulation seeds (16000 to 16019) on rosters `v1` and `book_v1`, which no earlier round used for this change.

| # | Prediction |
|---|---|
| F1 | zero certificate violations in every real cell, all four policies |
| F2 | honest cost at or under 1.25 points under `recommended_budget` in at least 95% of in-sample cells and at least 90% of hold-out cells (H1 and H2 pooled) |
| F3 | mean honest cost at 10 s under `recommended_budget`: at most 0.6 points in-sample (round 16, `recommended`: 1.26) and at most 0.8 on pooled hold-out |
| F4 | on the same cells at 10 s, the budget cuts `recommended`'s mean honest cost by at least 40% in-sample and at least 30% on pooled hold-out |
| F5 | simulated honest agents on `v1`: the largest mean honest cost of any agent under `recommended_budget` is at most 1.5 points (under `recommended` it is about 24, the slow-from-start agent) |
| F6 | simulated sustained runaways (profile `runaway`, `v1`): mean saved share under `recommended_budget` within 4 points of `recommended`'s |
| F7 | the price: on `book_v1`, at least one patient-pulser agent loses at least 10 points of saved share under `recommended_budget` against `recommended` (the 5-slice warm-up removes the early catch) |
| F8 | the honest pool's mean net burn on `book_v1` (agents that should not be tightened, `recommended_slice`) moves at least half the way to zero under `recommended_slice_budget` |

**What would falsify the change.** F5 or F6 missed by a wide margin (the fix costs more saving than it saves honest agents), or F1
failing (the budget can only raise a floor, so a violation would mean a bug).

**Limits, stated now.** No runaway labels in the real data, so the real half measures honest cost only. Hold-out agents are the same
people as the diagnosis agents. The floor reads completed-duration quantiles that exclude tasks already killed, so it is
conservative; the real-data cost can undershoot the budget.

## Round 17, results: the cost budget

Run once (`python experiments/real_trace_round17.py`, then `python experiments/score_round17.py`): 600 real cells and 40 simulated runs.
Raw: `experiments/real_trace_round17_real.json`, `experiments/real_trace_round17_sim.json`.

| # | Prediction | Result | Held? |
|---|---|---|---|
| F1 | zero certificate violations, all four policies | 0 of 600 | Yes |
| F2 | cost at or under 1.25 points in at least 95% of in-sample and 90% of hold-out cells | 97.9% in-sample; 98.1% hold-out (`recommended` alone: 83.3% and 96.3%) | Yes |
| F3 | mean cost at 10 s at most 0.6 in-sample, 0.8 hold-out | 0.32 and 0.27 (`recommended`: 1.26 and 0.59) | Yes |
| F4 | cost cut of at least 40% in-sample, 30% hold-out | 74% and 55% | Yes |
| F5 | largest simulated honest cost at most 1.5 points on `v1` | 1.32 (slow-from-start; `recommended`: 18.51) | Yes |
| F6 | sustained runaways lose at most 4 points of saved share | 25.3% to 21.0%: **4.3 points** | **No (by 0.3)** |
| F7 | a patient pulser loses at least 10 points on `book_v1` | **none**: pulse-8 was 0.7% and 0.7% (20.3% and 20.3% under the slice trigger) | **No** |
| F8 | honest pool net burn moves at least half way to zero | -14.24 s to -2.39 s per agent-run (83% of the way) | Yes |

**What round 17 says.**
- The budget does what it claims. Honest cost falls by 55% to 74% on real latencies, on held-out stretches as well as the diagnosis data, and pooled honest cost on the simulated `v1` roster falls from 1.99 to 0.19 points. The slow-from-start agents go from 18.5 and 12.3 points to 1.2 and 1.3. The worst real cell falls from 7.95 to 1.74.
- The price is measured: 4.3 points of saved hung time on sustained runaways (F6 missed by 0.3 points, reported as missed), 7.3 points on the inflate-first attacker (25.9% to 18.6%) and 3.4 on the low-and-slow attacker (13.6% to 10.2%). Those two attackers' own legitimate cost also falls (8.55 to 6.35, 0.16 to 0.10).
- F7 was wrong: I predicted the five-slice warm-up would cost the pulser its early catch. The period-8 pulser was not caught by `recommended` either way, and the slice trigger catches it at the same rate with and without the budget.
- Not predicted, observed: the share of real agents tightened barely moves at 10 s (88% to 81% in-sample, 89% to 89% hold-out). The budget does not stop the limiter acting on a heavy-tailed agent; it stops the action from costing that agent more than about 1% of its tasks.

**Limits.** Hold-out agents are the same developers as the diagnosis agents, read at later stretches. No runaway labels in the real data. The simulated rosters are the generator's; the budget was not tuned on seeds 16000 to 16019. The cost budget is off by default.

## Round 18, pre-registered: trying to break the certificate and the cost budget

Committed before the run (a one-seed smoke run was executed and deleted unread). Runner `experiments/red_team_round18.py`,
scorer `experiments/score_round18.py`. Four hand-built agents at a 100 ms deadline, 8000 tasks each, 20 seeds (18000 to 18019), four
policies (`recommended`, `recommended_budget`, `recommended_slice`, `recommended_slice_budget`):

- `stepchange-honest`: median 20 ms, jumping to 50 ms at task 3000 (a step inside one slice, which the budget's floor cannot see coming).
- `bandstack-honest`: the certificate's worst case: 98.5% of tasks in the top 27.1% band (76 to 82 ms) and 1.5% natural overruns, which
  trigger the rate rule.
- `floorpump-attacker`: completes slowly (90 to 99 ms) for 6 slices so that its own budget floor sits near the deadline, then hangs 5%.
- `sybil-0` to `sybil-5`: one attacker that takes a new agent identity every 6 slices, idle (1 ms tasks) outside its window, hanging 5% inside it.

| # | Prediction |
|---|---|
| G1 | the certificate is not broken: for every agent, policy and seed, honest cost is at most its bound and an attacker's saved share of hung time is at most 27.1%. One violation falsifies it as stated. |
| G2 | `bandstack-honest`: mean cost at least 90 points under `recommended` (the bound is about 98.5); at most 2 points under `recommended_budget` |
| G3 | `stepchange-honest`: mean cost under `recommended_budget` exceeds 1.0 point (the step breaches the budget before the floor catches up); low confidence, the agent may never be tightened |
| G4 | `floorpump-attacker`: mean saved share at least 15% under `recommended`; at most 3% under `recommended_budget` (the attacker switches off its own limiter) |
| G5 | `sybil`: saved share of the attacker's hung time pooled over its identities: between 10% and 22% under `recommended`; at most 3% under `recommended_budget` |
| G6 | under the budget, no attacker is ever worse than the static deadline (saved share never negative) |

**Stated assumption under test in G5.** The certificate is per agent identity. An attacker that can mint identities starts each one at the
default limits. If G5 holds, the certificate stands but the saving is only available to systems that bind identity.

## Round 18, results: trying to break the certificate and the cost budget

Run once (`python experiments/red_team_round18.py`, then `python experiments/score_round18.py`): 720 rows. Raw: `experiments/red_team_round18.json`.

| # | Prediction | Result | Held? |
|---|---|---|---|
| G1 | certificate not broken | 0 honest violations; no attacker run above 27.1% (highest single run 26.25%) | Yes |
| G2 | `bandstack-honest`: at least 90 points under `recommended`; at most 2 under `recommended_budget` | **79.6** (bound 98.5; 88.5 under the slice trigger); budget **1.07** | Half (the budget half) |
| G3 | `stepchange-honest` under the budget exceeds 1.0 point | **0.08**; the agent is barely tightened (final deadline ratio 0.989) and `recommended` costs 0.25 | **No** |
| G4 | `floorpump-attacker`: at least 15% under `recommended`; at most 3% under the budget | 24.3%; 1.0% | Yes |
| G5 | sybil pooled saved share: 10% to 22% under `recommended`; at most 3% under the budget | 11.4%; **8.5%** | Half (the `recommended` half) |
| G6 | no attacker worse than static under a budget policy | 0 of 360 attacker runs | Yes |

**What round 18 says.**
- The certificate survived the attempts. The worst honest agent (98.5% of its tasks inside the band) lost 79.6 points under `recommended`: close to, but under, its bound of 98.5, because the limiter needs several slices to reach the cap. The same agent lost 1.07 points under the budget, which is the budget doing exactly its job.
- The pump attack works as predicted and costs the attacker nothing: by completing slowly for six slices an attacker keeps its own deadline near the old one (final ratio 0.989) and the saving falls from 24.3% to 1.0%, which is the static result, not worse. The budget therefore gives up the saving against an attacker that can complete slowly, by design; the certificate's attacker bound is untouched.
- G3 missed: a step change inside one slice did not breach the budget, because the agent was almost never tightened (the drift hold and its natural tail kept the trap rate low), so this round did not exercise the lagging floor. The lag is untested, not shown harmless.
- **G5 is confounded and the budget half is not a fair test.** In this harness each identity reports idle slices before its window. Those idle reports satisfy the five-slice warm-up and drag its floor down, which gives the budget history a real new identity would not have. A truly fresh identity has no history, so under the budget nothing would tighten for its first five slices; that would push the saving toward zero but is **not measured here**. What round 18 does show: an identity change every 6 slices cuts the saving from about 24% to 11.4% under `recommended` (identity 5, with a 2-slice window, saved 0.3%). Identity binding is an assumption of the saving, not of the certificate.

**Open.** A fresh-identity sybil test needs the simulator to let an agent exist only from its first task. The lagging-floor test needs an honest agent that steps up while already tightened.
## Round 19, pre-registered: a fresh identity has no history

Committed before the run (a one-seed smoke run was executed and deleted unread). Round 18's identity test was confounded because each
identity reported idle slices before its window. The simulator now accepts an optional `active` trace column; a slice with no active
task is skipped for that agent, so an identity exists only from its first task and leaves silently (new test `tests/test_idle_slices.py`;
traces without the column behave exactly as before and the existing suite passes). Runner `experiments/red_team_round19.py`, scorer
`experiments/score_round19.py`. Agents, all attackers (fast tasks of 20 to 60 ms, 5% hangs): `persistent` (one identity, 32 slices,
the control), `sybil6-k` (a new identity every 6 slices) and `sybil3-k` (every 3 slices). 20 seeds from 19000; four policies as in round 18.
Saved share is pooled over an attacker's identities.

| # | Prediction |
|---|---|
| H1 | the certificate holds: no identity in any run saves more than 27.1% of its hung time |
| H2 | the persistent attacker (fast, so its budget floor is low) is saved from at least 20% under `recommended` and at least 15% under `recommended_budget` |
| H3 | `sybil6`, `recommended`: pooled saved share between 10% and 25% |
| H4 | `sybil6`, `recommended_budget` and `recommended_slice_budget`: at most 3% (a new identity has no history, so nothing tightens for its first five slices) |
| H5 | `sybil3`: `recommended` between 5% and 20%; the budget policies at most 1% |
| H6 | binding identity is worth at least 15 points of saved share under the budget: persistent minus `sybil6` |

**Stated limits.** The harness lets the attacker take a new identity for free; a deployed system may charge for identities. The attacker here
is not trying to defeat the limiter any other way. The honest-cost bound does not depend on identity and is not tested again here.

## Round 19, results: a fresh identity has no history

Run once (`python experiments/red_team_round19.py`, then `python experiments/score_round19.py`): 1440 rows, raw in `experiments/red_team_round19.json`.
Saved share of hung time, pooled over an attacker's identities:

| Attacker | `recommended` | `recommended_budget` | `recommended_slice` | `recommended_slice_budget` |
|---|---|---|---|---|
| persistent (one identity) | 26.1% | 22.3% | 26.2% | 23.0% |
| new identity every 6 slices | 20.5% | **2.1%** | 22.0% | **3.9%** |
| new identity every 3 slices | 15.0% | **0.0%** | 17.4% | **0.0%** |

| # | Prediction | Result | Held? |
|---|---|---|---|
| H1 | no identity above 27.1% | 0 of 1440 rows; highest 26.46% | Yes |
| H2 | persistent: at least 20% (`recommended`), at least 15% (budget) | 26.1%; 22.3% | Yes |
| H3 | `sybil6`, `recommended`: 10% to 25% | 20.5% | Yes |
| H4 | `sybil6`, both budget policies at most 3% | 2.1%; **3.9%** (slice variant) | Half (the slice variant missed by 0.9) |
| H5 | `sybil3`: `recommended` 5% to 20%; budgets at most 1% | 15.0%; 0.0% and 0.0% | Yes |
| H6 | binding identity worth at least 15 points under the budget | 20.2 points | Yes |

**What round 19 says.**
- The cost budget trades away resistance to identity changes. Without it, an attacker that takes a new identity every 6 slices keeps the limiter's saving at 20.5% (down from 26.1%). With it, the saving is 2.1%: a fresh identity has no history, so nothing tightens for its first five slices, and an attacker that rotates faster than that is never tightened. The same budget that cut honest cost by 55% to 74% is what makes the rotation pay.
- The certificate itself is untouched (H1). The honest-cost bound does not depend on identity.
- Round 18's 8.5% for the budget policy was the idle-history confound, as suspected: the fair figure is 2.1%.

**Consequence.** The cost budget should be deployed with identity binding (an identity that costs something to create, or one that inherits a starting history from its operator, team or tooling class), or a new identity should start at a conservative deadline until its history is known. Neither is built or tested. The budget stays off by default.

**Limits.** The harness gives the attacker free identities, which is the worst case. The attacker's behavior is fixed and fast; a real one would also pump its floor.

## Round 20, pre-registered: a provisional floor for a new identity

Committed before the run (a one-seed smoke run was executed and deleted unread). Round 19 found that the cost budget, which refuses to
tighten until five slices of history exist, lets an attacker that takes a new identity every few slices escape the limiter (2.1% saved
against 22.3% for one persistent identity). The change: `TrapPolicy(cost_budget_new_identity_ratio=0.9)` (off by default; two new
tests; the full suite passes). While an identity has fewer than five slices of history, a deadline tightening may go to 0.9 of the
configured deadline (one step) instead of being refused; with history, the budget's floor decides as before. This is designed after
seeing round 19's numbers, so those numbers are diagnosis data; the tests below use fresh seeds (20000 to 20019) and the round 17
hold-out stretches. Runner `experiments/red_team_round20.py`, scorer `experiments/score_round20.py`.

Policies added: `recommended_budget_prov`, `recommended_slice_budget_prov`. Agents: round 19's persistent, `sybil6` and `sybil3` attackers,
plus three honest newcomers (a fresh identity from slice 10, median 30 ms, natural tail about 2.3%).

| # | Prediction |
|---|---|
| I1 | the certificate holds: no attacker identity saves more than 27.1%, and honest cost never exceeds its bound (sim and real) |
| I2 | `sybil6`: pooled saved share 5% to 10% under `recommended_budget_prov`; 5% to 12% under `recommended_slice_budget_prov`. `sybil3`: 2% to 8% under `recommended_budget_prov` |
| I3 | the persistent attacker under `recommended_budget_prov` stays within 1.5 points of its `recommended_budget` saving (22.3% in round 19) |
| I4 | real hold-out stretches: mean honest cost at 10 s under `recommended_budget_prov` exceeds `recommended_budget`'s by at most 0.15 points; pooled over the three deadlines by at most 0.10 |
| I5 | honest newcomers: mean cost under `recommended_budget_prov` is at most half of `recommended`'s and within 0.5 points of `recommended_budget`'s |

**What would falsify the change.** I2 missing low (the provisional step buys no resistance to rotation) or I4/I5 missing high (it costs
honest identities what the budget was meant to save them). **Stated limit:** one provisional step bounds, not removes, the gain from
rotation: at most 10% of hung time per identity-window. An attacker that rotates every slice would still be held near zero by the
rate rule's need for a pattern.

## Round 20, results: a provisional floor for a new identity

Run once (`python experiments/red_team_round20.py`, then `python experiments/score_round20.py`): 2520 simulated rows and 162 real rows.

Saved share of hung time (attackers) and honest cost in points:

| | `recommended` | `recommended_budget` | `recommended_budget_prov` |
|---|---|---|---|
| persistent attacker, saved | 26.0% | 22.1% | 23.6% |
| new identity every 6 slices, saved | 20.5% | 1.9% | **9.3%** |
| new identity every 3 slices, saved | 15.1% | 0.0% | **6.6%** |
| honest newcomers, cost (bound 4.74) | 4.21 | 1.14 | **1.75** |
| real hold-out, 10 s, cost | 0.592 | 0.268 | **0.406** |

(The slice-trigger variants track these: 10.8% and 6.6% saved, newcomer cost 1.82.)

| # | Prediction | Result | Held? |
|---|---|---|---|
| I1 | certificate holds | 0 of 2520 attacker identity-runs above 27.1% (highest 26.51%); no honest cost above its bound (sim or real) | Yes |
| I2 | `sybil6` 5% to 10% (slice variant 5% to 12%); `sybil3` 2% to 8% | 9.3% (10.8%); 6.6% | Yes |
| I3 | persistent attacker within 1.5 points of 22.3% | 23.6% (1.3 points above round 19's figure; 1.5 above this round's `recommended_budget`) | Yes (barely) |
| I4 | real hold-out cost at most 0.15 above the budget at 10 s; at most 0.10 pooled | +0.138 and +0.059 | Yes |
| I5 | newcomers: at most half of `recommended`'s cost and within 0.5 points of `recommended_budget`'s | 1.75 against 4.21 (42%, half held); 0.61 above the budget's 1.14 (**missed by 0.11**) | Half |

**What round 20 says.**
- The provisional step recovers part of what round 19 lost to rotation: from 1.9% to 9.3% (every 6 slices) and from 0.0% to 6.6% (every 3). It does not restore it (20.5% and 15.1% without the budget). The ceiling is by design, one 10% step per identity-window.
- The price is small and measured: honest newcomers pay 1.75 points instead of 1.14 (the plain budget) or 4.21 (no budget); real hold-out developers pay 0.06 points more than the plain budget on average and 0.14 at the tightest deadline.
- The three settings now sit on one curve: no budget (most saving, most honest cost, one identity change costs the attacker little), plain budget (least honest cost, rotation nearly free for the attacker), provisional step (between the two on both). An operator picks by which side is hurting. Identity binding would still make the plain budget the better choice.
- The certificate held throughout (I1).

**Limits.** The provisional ratio (0.9) is one setting, not tuned. Newcomers are three simulated agents with one tail shape. Real data still has no runaway labels. The attacker takes identities for free.

## Round 21, pre-registered: class-conditioned budgeted deadlines on real latencies with labeled hangs

Committed before the run (a one-seed smoke run was executed and deleted unread). Runner `experiments/class_budget_round21.py`, scorer
`experiments/score_round21.py`.

**Why.** Every round so far limits the deadline of an agent as a whole, so the cap (27.1%) bounds both the saving and the honest cost, and
the wall stands: a legitimately slow agent and a mild runaway look alike from timeouts alone. Real tool calls differ by orders of
magnitude by tool (median 36 ms for `Read`, 5 s for `write_stdin`; 99th percentile 6.6 s for `Read`, 442 s for `Bash`). A deadline kept
per (agent, tool class) can be tight where the class is fast and loose where it is slow. Per-operation adaptive timeouts are established
practice; what this round tests is the budgeted, floored, one-way version and its certificate: a class deadline is
`clamp(1.5 x the class's own 99th percentile of completed durations, 0.25 D, D)`, so honest cost is at most the share of legitimate calls
in (0.25 D, D] and the saving at most 75% of hung time (the cap's 27.1% becomes a floor-set 75%).

**Data and labels.** Real calls keep their latency and tool name; hangs are injected and labeled: half of each population's agents are
runaway agents, which from slice 4 replace 2% of calls with a hang of uniform(3, 9) x D. Populations: wide (23 users, first 2000 calls) and long
(9 users, first 8000). Deadlines 10, 60, 180 s. 10 seeds from 21000. Regimes: the cap policies `recommended`, `recommended_budget_prov` and
`recommended_slice_budget_prov`; `class_oneway` (the deadline only falls, never rises) and `class_twoway` (re-read each slice, may
rise back toward D). The class regimes are a numpy prototype, not yet in the bridge. Class and cap results are over the same agents and seeds.

| # | Prediction |
|---|---|
| K1 | the certificates hold: honest cost at most its bound (cap: share in (0.729 D, D]; class: share in (0.25 D, D]) in every agent-run, and saved share of hung time at most 27.1% for cap regimes and 75% for class regimes |
| K2 | at D = 10 s, class_oneway saves at least 45% of the runaway agents' hung time (pooled), and at least 1.5 times `recommended_budget_prov`'s share |
| K3 | at D = 10 s, class_oneway's mean honest cost is at most 2.0 points (cap policies: about 0.4 to 0.6 in round 17 and 20) |
| K4 | the price of the one-way rule: at D = 10 s on the long population, class_oneway costs honest agents at least 0.3 points more than class_twoway |
| K5 | at D = 180 s, class_oneway saves at least 30% of hung time |

**What would falsify the idea.** K2 missing (class deadlines buy no more than the cap) or K3 missing high (the saving is paid for by honest
agents). **Stated limits:** latencies include time developers spent approving prompts; the hangs are synthetic; the class deadline reads
completed durations only, so a class whose tail is mostly killed is under-estimated; classes with fewer than 30 completions keep deadline D.

## Round 21, results: class-conditioned budgeted deadlines

Run once (`python experiments/class_budget_round21.py`, then `python experiments/score_round21.py`): 4800 rows over 10 seeds, 3 deadlines, 2 populations,
5 regimes. Raw: `experiments/class_budget_round21.json`.

Saved share of hung time on runaway agents / mean honest cost in points (pooled over seeds):

| | D | `recommended` | `recommended_budget_prov` | `class_oneway` | `class_twoway` |
|---|---|---|---|---|---|
| wide (23 users, 2000 calls) | 10 s | 22.9% / 1.27 | 15.5% / 0.49 | **39.6% / 0.99** | 37.1% / 0.59 |
| wide | 60 s | 19.7% / 0.64 | 15.1% / 0.32 | **51.7% / 0.77** | 48.9% / 0.70 |
| wide | 180 s | 11.5% / 0.32 | 8.8% / 0.16 | **57.6% / 0.70** | 56.4% / 0.64 |
| long (9 users, 8000 calls) | 10 s | 24.7% / 0.79 | 23.2% / 0.59 | **47.6% / 0.91** | 43.7% / 0.75 |
| long | 60 s | 22.9% / 0.18 | 22.6% / 0.17 | **67.3% / 1.54** | 65.2% / 1.33 |
| long | 180 s | 20.6% / 0.07 | 20.5% / 0.07 | **71.2% / 1.32** | 70.1% / 1.21 |

| # | Prediction | Result | Held? |
|---|---|---|---|
| K1 | both certificates hold; saving at most 27.1% (cap) and 75% (class) | 0 violations in 4800 rows; highest single run 27.1% (cap) and 75.0% (class, exactly the floor) | Yes |
| K2 | at 10 s, class_oneway saves at least 45% and at least 1.5 times `recommended_budget_prov` | 45.2%; 2.16 times | Yes (45.2 against a 45 threshold) |
| K3 | at 10 s, class_oneway honest cost at most 2.0 points | 0.97 (cap policies 0.52 to 1.13) | Yes |
| K4 | the one-way rule costs at least 0.3 points more than the two-way (long, 10 s) | 0.91 against 0.75: **0.17** | **No** |
| K5 | at 180 s, class_oneway saves at least 30% | 67.2% | Yes |

**What round 21 says.**
- Keeping the deadline per tool class roughly doubles the saving at 10 s (45.2% against 20.9%) and triples it at 180 s (67.2% against 8.8% to 20.6%), at a honest cost of about 1 point against about 0.5 for the budgeted cap policy and 1.1 for the unbudgeted one. The saving is no longer pinned to 27.1%; the floor (0.25 D) sets the ceiling, and a single class run reached exactly 75.0%.
- The one-way rule is cheaper than predicted (K4 missed): it costs 0.17 points more than letting the deadline rise again, on this data and these horizons. The erosion seen in round 15 did not show at 8000 calls.
- The worst single honest agent-run is higher with classes (5.8 points, against 2.5 under the budgeted cap policies), which is what a 0.25 floor allows (bound: the share of calls in the top 75% of the deadline).

**What it does not show.** The injected hangs are gross (3 to 9 times D), so any deadline under D catches them; the wall (a mild runaway that is slow but not hung) is not tested. Per-operation adaptive timeouts are established practice; the claim here is the one-way, budgeted, floored version with a computable bound, measured on real tool latencies. The class regime is a numpy prototype, not in the bridge, and has no audit chain or operator path. Latencies include human approval waits.

**Next, if the idea holds:** a mild-runaway injection (a class's calls inflated 2 to 4 times but still under D), and moving the class deadline into `TrapBridge` so it has the audit log and the single-use-grant loosening.

## Round 22 pre-registration: the wall (mild runaways)

Written and pushed before the full run. Smoke run (1 seed) was deleted unread.

**Question.** Round 21's class deadlines caught gross hangs. Do they catch a runaway that is slow but never hung?

**Protocol.** Same real tool latencies and populations as round 21 (wide, long). Half of each population is seeded as a runaway. From slice 4 on, 3% of a runaway's calls take 2 to 4 times as long, capped at 0.95 D, so a static deadline never kills them. They are labeled bad. The system cannot see the label: a bad call that completes enters the class's duration history like any other (poisoning). Regimes: recommended, recommended_budget_prov, recommended_slice_budget_prov, class_oneway, class_twoway. Deadlines 10, 60, 180 s. 10 seeds from 22000.

**Predictions.**
- L1: every certificate holds. Honest cost is at most the bound; saving is at most 27.1% for cap regimes and 75% for class regimes.
- L2: at D=60 s class_oneway kills at least 10% of mild-runaway calls; cap policies kill at most 3%.
- L3: at 60 s class_oneway saves at least 3 times the mild-runaway time that recommended_budget_prov saves, which is under 5%.
- L4: at 60 s class_oneway honest cost is at most 2.0 points.
- L5: on the long population at 60 s class_oneway saves at least 2 points more than class_twoway (poisoning hurts the two-way rule).
- L6: the wall is not breached: class_oneway saves less than 25% of mild-runaway time at every deadline.

Misses will be recorded as misses.

### Round 22 results

4,800 rows, 10 seeds, scorer: experiments/score_round22.py (table in experiments/score_round22.txt).

- **L1 held.** No honest agent exceeded its bound. Cap regimes never saved more than 27.1%.
- **L2 missed.** At 60 s class_oneway killed 6.1% of mild-runaway calls (predicted at least 10%). The cap policies killed up to 6.7%, not under 3%, so the contrast I predicted does not exist at 60 s.
- **L3 missed.** class_oneway saved 20.8% of mild-runaway time against 7.8% for recommended_budget_prov: 2.7 times, not 3 times.
- **L4 held.** Honest cost 1.07 points at 60 s.
- **L5 held.** Poisoning matters: on the long population at 60 s one-way saved 29.4% against 24.5% for two-way.
- **L6 missed.** The wall was breached on the long-tailed population: class_oneway saved 29.4% at 60 s and 37.4% at 180 s. On the wide population it stayed at 8% to 16%.

**Reading.** Class deadlines help against mild runaways only where an agent's normal calls are far shorter than D, so a 2 to 4 times slowdown is still far below the cap. Where the class's own normal tail is long, they save little. Honest cost stays near 1 point. This is a partial win, not a solution to the wall: a slow-but-legitimate class and a mild runaway still cannot be told apart by timing alone.

## Round 23 pre-registration: class deadlines inside TrapBridge

Written before the parity run. The bridge code and 11 unit tests (tests/test_class_deadlines.py) exist and pass; the full suite passes.

**Question.** Does the governed implementation (a boundary per agent and class, tightening only through the governor, loosening only through a named operator, every change audited) behave like the round 21 and 22 prototype?

**Protocol.** Round 22's mild-runaway data (same seeds 22000 to 22009, wide and long, D = 10, 60, 180 s). The prototype regime class_oneway is compared with the bridge regime on identical calls, per agent. The bridge is refreshed at each 250-call slice start, as the prototype re-reads.

**Predictions.**
- M1: aggregate saving of mild-runaway time matches the prototype within 1.0 point in every population and deadline.
- M2: honest cost matches within 0.2 points.
- M3: no class deadline ever rises in any run (checked on every boundary after every slice).
- M4: no honest agent exceeds its bound (the certificate), and no class deadline is below 0.25 D.
- M5: every class tightening has an audit entry (count of audit entries equals count of tightenings).
- M6: the bridge costs under 5 ms per slice refresh per agent on average.

### Round 23 results

960 agent-runs (seeds 22000 to 22009), scorer experiments/score_round23.py, table in experiments/score_round23.txt. All six predictions held.

- M1: largest saving gap between bridge and prototype 0.88 points (long, 10 s; the per-class history cap of the prototype versus unbounded here is the likely cause, not tested).
- M2: largest honest-cost gap 0.02 points.
- M3: 0 deadline rises. M4: 0 bound violations. M5: every tightening has an audit entry.
- M6: refresh averages 0.41 ms per agent per slice.

**Reading.** The governed version behaves like the prototype, so round 21 and 22 results carry over to the bridge. What the bridge adds is the audit trail and a loosening path that waits for a named operator. Still true: class deadlines do not solve the wall (round 22), and every run is simulation on one public trace.

## Round 24 pre-registration: a second signal (declared expectation)

Written before any run. No smoke run.

**Idea.** An agent (or its tool wrapper) may declare, at the start of a call, how long it expects the call to take. The kernel enforces min(class deadline, 3 x declared), never below the same 0.25 D floor. A declaration can only tighten a deadline, so the asymmetric rule holds: lying upward gains nothing (the class deadline applies anyway), lying downward only hurts the liar. The certificate is unchanged because the floor is unchanged (honest cost at most the share of calls in (0.25 D, D]; saving at most 75%).

**Honest limit of this test.** The public trace has no declared expectations. They are simulated as the call's intended duration times lognormal noise with sigma 0.5, 1.0 or 1.5 (an assumption about forecast quality, not data). A runaway's intended duration is its latency before inflation. Two runaway types: non-strategic (a bug: it declares its intended duration) and strategic (declares the inflated duration it will actually take). Results are conditional on that assumption.

**Protocol.** Round 22 data and runaway injection (3% of a runaway's calls inflated 2 to 4 times, capped at 0.95 D, from slice 4), wide and long, D = 10, 60, 180 s, 10 seeds from 24000. Regimes: class_oneway (baseline), decl_nonstrategic, decl_strategic, at each sigma. Honest agents' good calls always get a noisy honest declaration.

**Predictions.**
- N1: certificates hold (honest cost at most the bound; no class or declared deadline below 0.25 D).
- N2: at D=60 s, sigma 0.5, decl_nonstrategic saves at least 1.5 times the mild-runaway time class_oneway saves (averaged over both populations).
- N3: decl_strategic saves within 0.5 points of class_oneway at every sigma and deadline, and never less.
- N4: honest cost at sigma 0.5 is at most 0.5 points above class_oneway's.
- N5: honest cost at sigma 1.5 is more than 2 points above class_oneway's (poor forecasts hurt honest agents).
- N6: on the long population at 60 s, sigma 0.5, decl_nonstrategic saves at least 35% of mild-runaway time.
