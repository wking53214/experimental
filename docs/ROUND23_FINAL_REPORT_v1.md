# Round 23 final engineering report (v1)

Author of the work: Claude, for William N. King. Date: 2026-11-19. Branch `round-23-kernel-replay` (experimental PR #31); kernel changes in STACK-Kernel PR #95 (stack-p3-2).
Three conclusions are kept separate throughout, as the mission requires: (1) what a simulation showed, (2) what tests of real code showed, (3) whether any of it is ready for production. They are not the same thing, and none implies another.

## A. Executive assessment

**Recommendation: remain experimental.**

What changed:
- The Rust kernel (`stack-p3-2`) now reads a per-agent, per-tool-class deadline from the governance service, enforces its own floor (25% of its default) and ceiling (the default), and falls back to the whole-agent value whenever the class value is missing, junk, or the service is down. It can optionally keep its last good limits in a file so a restart does not forget them.
- The Python bridge writes class limits one way only (tighten), logs every decision (including refusals and holds) in the governor's hash-chained audit log, and cannot loosen without a named human's grant.
- Evidence was gathered on two public traces (UW TraceLab, Harvard FreeInference) and, for the first time, on real stuck calls (FreeInference calls marked `timeout`), plus a red-team round, a blue-team patch, and a pre-registered record of every prediction, including the misses.

Why it matters: the project's core idea (automation may only tighten, a named human loosens) is now enforced in two independent places (service and kernel), and every property claimed about it has a test that tries to break it.

What remains incomplete, in order of weight:
1. **The class-conditioned deadline did not hold up on real stuck calls.** On 1,482 real timeouts, one adaptive deadline per user saved more than a deadline per tool name (17% vs 10% of timeout time at 60 s). Every earlier class-deadline gain was measured against runaways that I injected. Those gains are real arithmetic about injected data; they are not evidence about real runaways.
2. **No live process feeds the bridge.** Everything beyond unit and replay tests is simulation on recorded traces.
3. **Duration alone cannot separate a stuck call from a slow honest one.** Across every change tried, saving was bought at about 8 to 11 points per point of honest cost. The only untested lever that could change that is a progress signal, which needs real sessions (the shadow test, setup guide `docs/SHADOW_TEST_SETUP_v1.md`).

## B. Architecture (before and after)

Before (whole-agent only):

```
 agent runtime --(trap events)--> TrapBridge (Python) --propose tighten--> Governor (audit log, grants)
                                                                              |
 stack-p3-2 kernel <--GET /boundaries/<agent>.deadline_ns-- governance service -+
      |  effective = min(governed, default); error => last good, else default
      v
  one deadline for every call of the agent
```

After (class-aware, optional per call):

```
 agent runtime --(completed durations by tool class)--> TrapBridge
      one-way target = clamp(1.5 x class p99, 0.25 x D, D), min with previous, needs 30 completions
      every decision (applied, unknown class, breaker hold, not applied) --> Governor audit log (written first)
                                                                              |
 stack-p3-2 kernel <--GET /boundaries/<agent>.class.<class>.deadline_ns-------+
      effective_for_class = min(class value held to [0.25 D, D], agent value, default)
      no class / empty / unknown / junk / service down => exactly the whole-agent result
      optional snapshot file: last good values survive a restart; the service's answer always wins when reachable
```

Responsibility boundaries (criterion 12): the adaptive component only proposes tightening; the governor alone records and applies; a named operator alone loosens; the kernel only enforces, and clamps whatever it reads. Class names use the same hashing rule as agent ids (the `h-` prefix is reserved on both sides; the Rust side did not reserve it at first, see S10).

## C. Integration (files, interfaces, paths)

experimental repo:
- `integrations/stack_traps.py` (TrapBridge): class deadline policy, `observe_class_durations`, `class_deadline`, `refresh_class_deadlines`, `request_class_loosening`, decision counters, audit of every decision.
- `tests/test_class_deadlines.py` (14 tests), `tests/test_round23_adversarial.py` (12 tests).
- `experiments/`: class bridge parity, kernel replay, comparison, FreeInference loader and replication, real-timeout and purple-team scripts, diagnosis; raw JSON/CSV for each.
- `docs/`: `TRACE_TEST_PLAN.md` (all pre-registrations and results), `ROUND23_ARCHITECTURE_PROPOSAL_v1.md`/`v2`, `SHADOW_TEST_SETUP_v1.md`, this report.

STACK-Kernel (PR #95), crate `stack-p3-2` only:
- `src/governed_limits.rs`: class ids, `effective_for_class`, kernel-side floor `CLASS_DEADLINE_FLOOR = 0.25`, class cache limit 4,096, opt-in `with_snapshot`.
- `src/lib.rs`: `begin_transaction_governed_class`.
- `tests/class_deadlines.rs` (20 tests), `examples/class_replay.rs`, `GOVERNED_LIMITS.md`.
- Not modified: `stack-admission`, `stack-issuer`, `stack-receipt`, CNS (inspected read-only). Authorization and receipts remain separate from limit loosening.

Execution path actually exercised: Python bridge writes slice limits, the Rust example makes every kill decision per call with `effective_for_class`, results are compared with the Python-side decision. This is a replay through real kernel code, not a live agent.

## D. Governance: invariant by invariant

| Invariant | Result | Evidence | Remaining gap |
|---|---|---|---|
| Authority preservation | HOLDS | governor rejects any non-lowering value; kernel clamps values above default; 0 of 200+ loosening attempts succeeded | none found |
| No autonomous loosening | HOLDS | adversarial tests: no grant, wrong or missing credentials, a machine source approving itself, replayed grants, policy self-edit, direct store writes, boundary re-creation | grant authenticity depends on the operator registry (documented in `DURABLE_STATE.md`) |
| Fail-closed (meaning here: fall back to the safe baseline, not the strictest value) | HOLDS | invalid values ignored, last good kept, service-down returns default (Rust tests) | restart with service down and no snapshot returns the default (F3, documented) |
| Auditability | HOLDS in replay | 1,192,416 of 1,192,416 and 125,742 of 125,742 class decisions logged, counted from the governor's own log | first run missed (98.1%): breaker holds logged once per episode; fixed. Not tested live. |
| Restart safety | PARTIAL | governor replays and refuses to start on a broken chain; tampered-log test; kernel snapshot built and unit-tested | bridge history is not persisted (it relearns, limits stay); snapshot not tested for crashes mid-write or shared files |
| Identity resistance | PARTIAL | rounds 19 and 20 (simulation); class-id parity vectors | identity binding is not built |
| External-signal isolation | HOLDS | source tokens can only tighten or request loosening | no runtime heartbeat or declaration path exists yet |
| Enforcement independence | HOLDS | kernel floor and ceiling hold whatever the service says; 0 below-floor hits in 20M replayed calls | kernel trusts the transport (HTTP without TLS, loopback only) |
| Bounded adaptation | HOLDS | service floor 20%, kernel class floor 25%; honest cost never above the certificate bound in any arm of any run | the bound is a ceiling, not a typical cost |
| Fallback integrity | HOLDS | whole-agent path unchanged; 800 Python and 56 Rust tests pass; no class returns exactly the whole-agent value | none |

## E. Adversarial testing

Python (`tests/test_round23_adversarial.py`, 12 tests, 200+ counted attempts): all passed. Rust (`tests/class_deadlines.rs`, 20 tests): junk values, ceiling, floor, cache flood, fallbacks, id parity, snapshot cases. Red team on the per-user deadline (`experiments/purple3_red.py`): evasion bounded per call (held); poisoning with 30 slow calls blocked tightening for 3,000+ calls (my prediction missed); starvation weaker than predicted (deadline fell only to 0.82 D) and inside the certificate bound (my prediction missed). A window of the last 1,000 completions limits how long poisoning lasts.
Defects found that I had not predicted (S10): Rust did not reserve the `h-` id prefix; breaker holds were not audited per request; one setup call added log noise; my first tamper test did nothing because the governor already verifies its chain at startup.
Reproduce: `python -m pytest tests/`, `cargo test -p stack-p3-2`, and the scripts named in `docs/TRACE_TEST_PLAN.md` with the seeds recorded there; raw rows are committed.

## F. Performance (simulation, with its limits)

- Cross-language parity: 0 mismatches in 3,540,000 (TraceLab) and 16,320,000 (FreeInference) calls.
- Injected mild runaways, class path at 60 s: TraceLab saved 12% to 29%, honest cost about 1 point; FreeInference saved 11% to 12% (17% at 180 s), honest cost 0.8 to 1.25 points. Whole-agent policies saved 9% to 13%.
- At 10 s the class path is the weakest on saving (FreeInference 5% vs 15%).
- Real timeouts (stage 1 and 2): one adaptive deadline per user beat per tool name (17% vs 10% at 60 s; 26% vs 21% at 180 s). The saving swings by about 10 points between halves of the users because the 1,482 timeout calls come from few users.
- At 60 s only 3 of 20 users' deadlines tightened at all on their own stream; their own 99th-percentile call already exceeds 60 s.
- Splitting classes by program did not beat the saving-per-cost curve and missed two of five predictions. Every parameter change moves along one curve of about 8 to 11 points of saving per point of honest cost.
- False positives are honest calls killed (the cost figures above). False negatives are the unprotected share: 43% to 61% of runaway time sat in warm classes but was too short to catch; 7% to 8% in classes not yet warm.
- Statistical limits: injected runaways are my model of a runaway; parallel calls in the FreeInference trace share one latency value; user ordering uses request start times; "timeout" means someone gave up on a call, not proof it was a runaway; most results are over a few hundred to a few thousand users or calls with 3 to 10 seeds.

## G. Documentation

Updated in this change: `README.md` (Round 23 pointer), `docs/STATUS.md` (Round 23 section), `docs/SUCCESS_CRITERIA.md` (Round 23 criteria and results), `docs/TRACE_TEST_PLAN.md` (everything), this report, and `docs/SHADOW_TEST_SETUP_v1.md`. `docs/INTEGRATION.md` and `docs/ARCHITECTURE.md` are NOT yet rewritten for the class path; they still describe the whole-agent path only (an outstanding documentation gap).
Role in ≡TACK: a bounded governor for execution limits. It proposes only tightenings, keeps an audit trail, and leaves loosening to a named person. It is not an authorizer and does not touch admission, issuing or receipts.

## H. Outstanding work, risks, production blockers

1. No live agent feeds the bridge; nothing here ran in production or in a live loop for the class path.
2. The class label comes from the request. Who sets it and how (tool name versus parsed command) is unsolved, and a caller who writes the label can pick a favorable one.
3. Evidence on real stuck calls is thin (1,482 timeouts, few users, two halves disagree by about 10 points).
4. Duration cannot tell stuck from slow honest; the progress-signal idea is simulated only; the shadow test has not started.
5. The kernel snapshot is opt-in, unit-tested only, unencrypted (a writer can tighten down to the floor, not loosen), not tested for crashes mid-write.
6. HTTP without TLS between kernel and service; loopback or trusted network only.
7. The Python bridge does not use the snapshot; the bridge's own history is not persisted.
8. Token and memory limits are still not enforced by the kernel; C++ headers read nothing.
9. `INTEGRATION.md` and `ARCHITECTURE.md` need the class path added.
10. CI: one flaky wall-clock test in an unrelated crate (`anc-wall-clock`) failed once on PR #95 and passed on re-run; it should be tightened separately.
11. The merge order of stacked PRs and the kernel PR is a decision for William.

## Acceptance criteria (mission section 11)

| # | Criterion | Result |
|---|---|---|
| 1 | Connected to a real execution-control path | PARTIAL: real kernel code enforces the limits in a replay; no live agent |
| 2 | Enforcement independently validates | MET (kernel floor, ceiling, fallback; tested) |
| 3 | Existing invariants intact | MET (800 Python and 56 Rust tests pass) |
| 4 | Unauthorized loosening rejected | MET (200+ attempts, 0 successes) |
| 5 | Invalid state fails safely | MET |
| 6 | Restart safety | PARTIAL (governor yes; kernel snapshot unit-tested; bridge history not persisted) |
| 7 | Audit complete and inspectable | MET in replay (100% after one recorded miss); not tested live |
| 8 | Improves a measure without unacceptable disruption | NOT MET for the class split on real stuck calls (worse than one deadline per user); met on injected runaways; met for a per-user one-way deadline at about 1 point of honest cost |
| 9 | Whole-agent baseline remains a fallback | MET |
| 10 | Adversarial results reproducible | MET (scripts, seeds, raw files committed) |
| 11 | Documentation accurately describes the implementation | PARTIAL (see G) |
| 12 | Clear bounded responsibility | MET |

A simulation passing these criteria does not count as runtime integration; a test passing does not establish production readiness; a successful integration would not show the algorithm is optimal. Here the first two are stated outright above and the third is not claimed.

## I. Scorecard (0 to 10, each explained)

| Dimension | Score | Why |
|---|---|---|
| Architectural correctness | 7 | Clear boundaries, two independent enforcement points, safe fallbacks. Not higher because the class label's origin and trust are unresolved. |
| Governance preservation | 8 | No invariant weakened; 200+ attempts to loosen all failed; every decision audited in replay. Not higher because identity binding is unbuilt and grants rest on the operator registry. |
| Adaptive detection | 3 | Duration-only detection saves 10% to 30% on real stuck calls at about 1 point of honest cost, unevenly across users; the class split did not help on real data; the one idea that could do better is untested. |
| Adversarial robustness | 6 | Strong against loosening and forged state; weak to cheap poisoning (30 calls block tightening for thousands of calls) until windowed; evasion is bounded but not made cheap. |
| Integration maturity | 3 | Real kernel code, replay-level only; no live loop; snapshot unproven under crashes; docs for the class path partly missing. |
| Experimental rigor | 7 | Predictions committed before runs; 7 misses on this stage and the S7 miss recorded; fresh seeds; raw rows published; outside data and real labels used. Not higher because most runaways were injected, and the real labels are few and ambiguous. |
| Documentation quality | 6 | Plan, proposal, results and guide are complete and honest about misses; `INTEGRATION.md` and `ARCHITECTURE.md` lag. |
| Production readiness | 2 | No live use, thin real evidence, unresolved label trust, no TLS, opt-in unproven snapshot. |

## J. Recommendation

**Remain experimental.** Evidence for: governance properties hold and are well tested; the kernel enforces independently; a bounded one-way deadline per user removes a modest, repeatable share of real stuck-call time at about 1 point of honest cost, with the certificate intact in every run. Evidence against moving on: the class-conditioned design, which is what this round set out to integrate, did not beat a simpler per-user deadline on real stuck calls; no live integration exists; the remaining lever (a progress signal) is unmeasured. Rejecting the approach is not warranted: the governance machinery is sound and the simpler design is supported.

Next steps in order of value: run the shadow test for two weeks; simplify to the per-user one-way deadline with the 1,000-call window as the supported design and mark the class path experimental; add the class path and snapshot to `INTEGRATION.md` and `ARCHITECTURE.md`; run a live loop against one real agent.
