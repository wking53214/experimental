# Where the project stands (2026-10-14, updated after the multi-seed replays)

One page. Evidence is in the linked documents; nothing here is new measurement.

## The question

Can a governance layer that lets automation only **tighten** limits (and only a named human **loosen** them) be useful for the STACK, and does it work?

**The claim, as of 2026-10-14.** Two claims were in play and they have come apart. The strong one, that the system judges on its own *when* a limit should come down, was tested in four rounds of trace replays and did not hold: statistical detection cries wolf on real telemetry, and no trap-count rule separates a legitimately slow or wide-tailed agent from a mild runaway. The bounded one is what the project now claims and measures (criterion 10): automation tightens within a fixed budget on strong, cheap signals; a named human loosens; and a validation layer judges every tightening afterwards from an independent signal, so a wrong tightening costs a person one decision rather than an outage. Human effort is relocated to adjudication, not removed. This is the reframing the research proposal's own critique asked for.

## What is established

| Claim | Status | Basis |
|---|---|---|
| Automation cannot loosen a limit, and every change is attributable | **Holds within the stated threat model** | Criterion 1: 36 of 36 soak runs plus fuzzing; criteria 7 and 8 (restart, outside signals). 733 tests pass locally (plus 46 that need DGK). Not covered: code inside the process, network attackers, malicious operators (`docs/THREAT_MODEL.md`). |
| It can govern a real system's limits | **Shown for DGK and for the Rust kernel's deadline** | DGK's three hardcoded limits run governed against the real DGK (46 tests); the Rust `stack-p3-2` kernel reads governed budgets over HTTP and obeys them (`docs/STACK_CLOSED_LOOP.md`). |
| It finds real defects | **Yes** | NaN/infinity handling in DGK, found by running its code and fixed on DGK `main` (`4a0ecd2`). |
| It saves something against a runaway | **Yes, bounded** | 27% less time spent on runaway tasks, exactly the breaker cap's arithmetic (3 steps of 10%). A perfect human does better on sustained attacks. |

## What is not established, or failed

| Claim | Status | Basis |
|---|---|---|
| Statistical detection beats simple baselines | **No** | Criterion 2 passes at the bare minimum (6 of 8) and clearly beats nothing. |
| Detection-driven tightening does not cry wolf on real telemetry | **Fails** | Criterion 3: 0 of 12 machines. Criterion 4: 0 of 6. False alarms spend the breaker before real trouble arrives. |
| Trap events avoid that problem | **Only partly** | Real enforcement events are real, but with the default policy a healthy agent was still tightened to the cap (10x more of its own legitimate timeouts) and a legitimately slowed agent had its failures more than doubled. A rate rule fixed the healthy case; a drift rule fixed the regressed case (unsmoothed only); an agent that is slow from the first round is still harmed. Now 20 seeds per roster (in a Python model of the kernel, held to the kernel's own decisions): the picture holds, with one more defect found and fixed (a recovered regression was tightened on the slow period's evidence) and one more limit made explicit (an agent whose natural tail is above the 1% rule is tightened every time, and no trap-count rule can tell it from a mild runaway). All simulated durations; thresholds picked for this workload, and the sweep shows the rate threshold is a workload parameter with agents on both sides of it. |
| Works on production traffic | **Not tested** | No real trap data exists in the library. |
| Fully integrated in the kernel | **Partly** | Only the deadline is enforced; token and memory limits are readable but not enforced; the C++ headers read nothing. |

## Trace replays (`docs/TRACE_TEST_PLAN.md`)

Two committed synthetic traces (`testdata/traces/`: 200,000 tasks over 25 agents, and 56,000 over 7 agents) replayed through the real Rust kernel. Of 16 agents that should not be tightened: default policy tightened 10, rate rule 8, rate+drift 3 (the remaining three: two agents slow from the first task, one with a 0.6% natural tail). All 9 agents that should be tightened were. Runaway cost down 24-25%. The drift rule can be defeated (T15): three attackers that inflate completions before they hang tasks were never tightened.

**Recommended configuration today** (the `recommended` policy in `experiments/stack_trace_replay.py`): `TrapPolicy(min_rate=0.01, drift_factor=1.5, drift_recent_reports=1, drift_excess=True, reasons=("deadline_exceeded",))` with the expected-load hook. On the three traces it left 2 to 4 of the agents that should not be touched tightened (down from 6 to 10 under the default policy), tightened every attacker including the ones that slow their completions first, and saved 21-24% of hung-task time. Known misses: agents slow from their first task (no baseline), an agent with a natural tail right at the 1% threshold, and gradual legitimate drift occasionally released (1 of 11). The slowdown estimator saturates around 4x. A first version of the excess rule failed its own pre-registered check and was corrected after the fact (`docs/TRACE_TEST_PLAN.md`).

**Fourth round, 20 seeds per roster** (`experiments/stack_trace_multiseed.py`, through `experiments/stack_trace_sim.py`, a Python model of the kernel that reproduces every committed kernel decision): runaway agents tightened in 100 of 100 agent-seeds, inflate-first attackers in 120 of 120 under the recommended policy (0 of 120 under the plain hold, the T15 hole on every seed), hung time saved 15-26% on runaways and 6-24% on attackers that inflate first (the hold's delay). False tightenings of agents that should be left alone: healthy agents with tails at or below 0.14% never (0 of 180); one "healthy" agent with a 1.5% natural tail always; the 0.6% tail agent 40% of the time; slow-from-start always; legitimate regressions and drifts in 39 of 220 agent-seeds, 11 of them one defect (a regression that recovers was tightened right after recovering, because the rate window still held the slow period's traps once the hold lifted) now fixed and checked on fresh seeds, the rest the excess rule's late, cheap false releases (about 1 in 8). The pre-registered robustness bar (four criteria) was **not met**: two of four failed, for those two reasons. Sweep: the 1.5x drift factor is flat down to 1.3 and breaks at 2.0; the 1% rate threshold has labelled agents at 0.6%, 0.8%, 1.5% and 2% on both sides of it, and no setting separates them. The model also found that the kernel example did not act on a tightened memory cap in the committed run (finding 1 stands either way).

## Scorecard (pre-registered, `experiments/scorecard.py`)

PASS: 1 safety, 2 detection vs baseline (bare minimum), 5 value vs alternatives (43% vs 40% needed), 6 poisoning (opt-in trimmed fit only), 7 restart, 8 signals, 9 integrations.
FAIL: 3 no crying wolf, 4 responds to real trouble, 10 bounded autonomy with validated tightening (added 2026-10-14; fails only on how fast a wrong tightening is confirmed: 54 of 128 within 6 slices against 80% needed; every other part passes).

The trap-loop work now has a criterion (10), written before the validation layer was built. Rounds 1 to 4 of it were developed after seeing results and are scored only against the predictions in `docs/TRACE_TEST_PLAN.md`. The trace-replay predictions were written before each run and are scored in `docs/TRACE_TEST_PLAN.md`.

## Decision recorded: cold-start agents are an accepted limit

An agent that is slow from its first task has no earlier baseline, so it is treated like any other high-trap agent and tightened (up to the 27% breaker cap) until a named person loosens it. In the traces this affected 3 agents (2 in the first trace, 1 in the held-out trace; timeouts roughly doubled, e.g. 15.2% to 33.3%). A learning period that holds tightening for a new agent's first ~1,250 tasks was considered and not built: it would remove those false tightenings but also delay real protection for every new agent by the same period. Revisit if slow-from-the-start agents turn out to be common.

**Fifth round, the validation layer** (question C of the research proposal; `TrapPolicy(validate_after=4)`): while a deadline is tightened, 10% of the agent's tasks stay on the original deadline, and every 4 slices the bridge compares the two groups' trap shares with confidence bounds; DEGRADED files a restore request for a person and holds further tightening. First version had no bounds and asked a person to undo 355 of 400 tightenings, including nearly every correct one. With bounds (checked on fresh seeds): correct tightenings are left alone (7 of 80 sustained runaways wrongly flagged), every slow-from-start agent is flagged on every seed (48 of 60 within 6 slices, the rest a slice or two later), wide-tail agents are flagged after 20 or more slices, late-released regressions are not resolved before the trace ends, and benefit is almost never confirmed (most correct tightenings read UNCHANGED, meaning "cannot tell" at this canary volume). Criterion 10 fails on that speed. The verdict is blind to intent: attackers that inflate their completed durations get restore requests a person must refuse (46%).

**Sixth and seventh rounds, pricing the two levers** (`experiments/stack_trace_multiseed_results_round6.json`, `..._round7*.json`): a 30% canary confirms every slow-from-start tightening within 6 slices (59 of 60, from 38 at 10%) and confirms benefit on a third of runaways (from a tenth), at the price of a third of the saving (16% instead of 22%). Operator-set per-class thresholds remove the 0.6%-tail false tightening and catch the low-and-slow attacker every time, and halve the 1.5%-tail case. Criterion 10 still fails at every configuration, on confirmation speed for late-released regressions and the wide-tail agent; a review-cadence change built on a wrong diagnosis was checked on fresh seeds, did nothing, and was reverted. The runaway half of criterion 10 moves between 4% and 14% across seed sets on identical code, so that bar is inside seed noise at this size. The real kernel, given the canary, matches the model on every decision and restore request; its limit client was found to have ignored every tightened memory cap in the committed v1 run (it refused fractional values) and is fixed in STACK-Kernel PR #9.

**Eighth round, gaming the canary (T16):** with the canary chosen by sequence number, an agent that completes exactly those tasks and hangs the rest gets a correct tightening declared DEGRADED on every review and a restore request filed on every seed; with a keyed selection the same agent is treated like an honest runaway. Keyed selection is now an option in the policy and the kernel example, and the deployment checklist says to set it.

## Known open issues, in the order I would worry about them

1. **Nothing has run on real traffic.** Every closed-loop number comes from simulated durations. The 1.5x drift factor is not on an edge in the sweep; the 1% rate threshold is a workload parameter (agents the labels put on both sides of it), and nothing in the trap stream can set it.
2. **The benefit is small and capped, and validation eats into it.** The design buys at most 27% on a runaway, 22% with a 10% canary, 16% with a 30% canary; it needs a human for everything else. The canary share is the one lever that trades saving for confirmation speed, and it is priced in `docs/TRACE_TEST_PLAN.md` (round 6) for an operator to choose; `docs/DEPLOYMENT_CHECKLIST.md` lists the rest.
3. **Baselines can be poisoned** (T2), and the drift rule can be defeated by an attacker who inflates completions first (T15, demonstrated). Opt-in mitigation only.
4. **Kernel coverage is thin.** Deadline only; C++ untouched; STACK-Kernel CI covers one crate. The kernel example now runs the validation canary and floors fractional limits (PR #9); token and memory caps are still enforced by the example, not the kernel.
5. **Audit anchoring is now a hook, not a decision** (`anchor_store`: the head is published after every append and checked at startup; a rewrite, truncation or deletion of the log is refused). Where the anchor file lives is still the operator's call, and on the same host under the same user it buys nothing. DGK has the same gap. Seven separate hash-chained ledgers exist in the library.
6. **No TLS** on the adjudication server or the kernel's HTTP read (T10).

## What it is for

The distinctive, working piece is the **one-way ratchet enforced by the store**: limits that adapt, with monotonicity guaranteed in code and audit, and a single human gate to reverse it. That fits the adaptation half of the STACK P3.2 proposal. The part that decides *when* to tighten is the weak part, and the honest current answer is: enforcement events plus a rate rule plus a drift check, a canary-based review of every tightening, and a human for the rest. The system is self-hardening in the safety direction and not self-judging; it should be described as bounded autonomy with human-gated loosening, never as a system that learns the right limits on its own.

## Where the code is

- `experimental` `main`: the core, integrations, experiments, docs. `experiments/stack_trace_sim.py` is the kernel-free replay path; `tests/test_trace_sim.py` holds it to the kernel's committed results.
- `STACK-Kernel` `main`: `crates/stack-p3-2/src/governed_limits.rs`, `examples/governed_workload.rs`, CI for that crate.
- `DGK` `main`: telemetry validation (`4a0ecd2`).
