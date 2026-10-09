# Where the project stands (2026-10-09, updated after the trace replays)

One page. Evidence is in the linked documents; nothing here is new measurement.

## The question

Can a governance layer that lets automation only **tighten** limits (and only a named human **loosen** them) be useful for the STACK, and does it work?

## What is established

| Claim | Status | Basis |
|---|---|---|
| Automation cannot loosen a limit, and every change is attributable | **Holds within the stated threat model** | Criterion 1: 36 of 36 soak runs plus fuzzing; criteria 7 and 8 (restart, outside signals). 719 tests pass locally. Not covered: code inside the process, network attackers, malicious operators (`docs/THREAT_MODEL.md`). |
| It can govern a real system's limits | **Shown for DGK and for the Rust kernel's deadline** | DGK's three hardcoded limits run governed against the real DGK (46 tests); the Rust `stack-p3-2` kernel reads governed budgets over HTTP and obeys them (`docs/STACK_CLOSED_LOOP.md`). |
| It finds real defects | **Yes** | NaN/infinity handling in DGK, found by running its code and fixed on DGK `main` (`4a0ecd2`). |
| It saves something against a runaway | **Yes, bounded** | 27% less time spent on runaway tasks, exactly the breaker cap's arithmetic (3 steps of 10%). A perfect human does better on sustained attacks. |

## What is not established, or failed

| Claim | Status | Basis |
|---|---|---|
| Statistical detection beats simple baselines | **No** | Criterion 2 passes at the bare minimum (6 of 8) and clearly beats nothing. |
| Detection-driven tightening does not cry wolf on real telemetry | **Fails** | Criterion 3: 0 of 12 machines. Criterion 4: 0 of 6. False alarms spend the breaker before real trouble arrives. |
| Trap events avoid that problem | **Only partly** | Real enforcement events are real, but with the default policy a healthy agent was still tightened to the cap (10x more of its own legitimate timeouts) and a legitimately slowed agent had its failures more than doubled. A rate rule fixed the healthy case; a drift rule fixed the regressed case (unsmoothed only); an agent that is slow from the first round is still harmed. All simulated durations, one seed, thresholds picked for this workload. |
| Works on production traffic | **Not tested** | No real trap data exists in the library. |
| Fully integrated in the kernel | **Partly** | Only the deadline is enforced; token and memory limits are readable but not enforced; the C++ headers read nothing. |

## Trace replays (`docs/TRACE_TEST_PLAN.md`)

Two committed synthetic traces (`testdata/traces/`: 200,000 tasks over 25 agents, and 56,000 over 7 agents) replayed through the real Rust kernel. Of 16 agents that should not be tightened: default policy tightened 10, rate rule 8, rate+drift 3 (the remaining three: two agents slow from the first task, one with a 0.6% natural tail). All 9 agents that should be tightened were. Runaway cost down 24-25%. The drift rule can be defeated (T15): three attackers that inflate completions before they hang tasks were never tightened.

**Recommended configuration today:** `TrapPolicy(min_rate=0.01)` with the expected-load hook; leave `drift_factor` unset unless regressions are common and attackers who slow their own tasks are not a concern; enforce only the deadline limit (tightening token and memory caps added collateral and no benefit in the trace). A trap-rate-aware hold (hold only while the trap rate stays at what the median shift explains) is the next thing to try and is not built.

## Scorecard (pre-registered, `experiments/scorecard.py`)

PASS: 1 safety, 2 detection vs baseline (bare minimum), 5 value vs alternatives (43% vs 40% needed), 6 poisoning (opt-in trimmed fit only), 7 restart, 8 signals, 9 integrations.
FAIL: 3 no crying wolf, 4 responds to real trouble.

The trap-loop work (rate rule, drift rule) is not part of the pre-registered criteria; it has no pass bar and was developed after seeing results. The trace-replay predictions were written before each run and are scored in `docs/TRACE_TEST_PLAN.md`.

## Known open issues, in the order I would worry about them

1. **Nothing has run on real traffic.** Every closed-loop number comes from simulated durations. The 1% rate threshold and 1.5x drift factor are untested outside it.
2. **The benefit is small and capped.** The design buys at most 27% on a runaway and needs a human for everything else.
3. **Baselines can be poisoned** (T2), and the drift rule can be defeated by an attacker who inflates completions first (T15, demonstrated). Opt-in mitigation only.
4. **Kernel coverage is thin.** Deadline only; C++ untouched; STACK-Kernel CI covers one crate.
5. **Audit anchoring is undecided** (head hash must live somewhere the governor cannot write; DGK has the same open gap). Seven separate hash-chained ledgers exist in the library.
6. **No TLS** on the adjudication server or the kernel's HTTP read (T10).

## What it is for

The distinctive, working piece is the **one-way ratchet enforced by the store**: limits that adapt, with monotonicity guaranteed in code and audit, and a single human gate to reverse it. That fits the adaptation half of the STACK P3.2 proposal. The part that decides *when* to tighten is the weak part, and the honest current answer is: enforcement events plus a rate rule plus a drift check, a human for the rest.

## Where the code is

- `experimental` `main`: the core, integrations, experiments, docs.
- `STACK-Kernel` `main`: `crates/stack-p3-2/src/governed_limits.rs`, `examples/governed_workload.rs`, CI for that crate.
- `DGK` `main`: telemetry validation (`4a0ecd2`).
