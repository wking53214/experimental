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
