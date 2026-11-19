# Round 23 architecture proposal (v1): governed class deadlines in the kernel path

Status: PROPOSAL. Nothing described under "Proposed" is built. Written after Phase 1 (baseline) and Phase 2 (inspection) of the Round 23 mission; waits for approval before any change to STACK-Kernel.

Naming: the mission calls this Round 23. The experimental repo already has a "bridge round 23" (PR #24: class deadlines inside `TrapBridge`, parity-tested against the prototype). This document calls that work "the bridge" and the mission's work "kernel integration".

## 1. Baseline (Phase 1)

| Item | Value |
|---|---|
| experimental `main` | 65d931a (PR #20, round 19). PRs #21 to #28 (rounds 20 to 26, bridge round 23, declared expectation, shadow tooling) are open, stacked, not merged |
| Working branch tip when inspected | f718fdc (shadow-output-growth), tree clean |
| Python tests | full suite passes on f718fdc |
| Environment | Python 3.13.18, numpy 2.5.3, pytest 9.1.1 |
| STACK-Kernel | d01ea36, 494 files (Rust crates, C++). Read only. Rust tests NOT run |
| CNS | 6e713e5, read only |
| Not accessible | nothing was refused; the kernel and CNS were inspected from shallow clones |

## 2. What is implemented, simulated, proposed

| Mechanism | State | Evidence |
|---|---|---|
| One-way limit store, operator-only loosening, audit chain, restart replay | Implemented (Python governor) | tests/test_durable_state.py, test_red_team_fixes.py, test_tightening_limits.py |
| Kernel reads per-agent deadline, token, memory limits; takes min(governed, default) | Implemented (Rust `stack-p3-2`, `governed_limits.rs`) | Rust unit tests in that file (not run by me) |
| `TrapBridge` tightening from trap events | Implemented, driven only by the replay simulator and tests | tests/test_integration_stack_traps.py |
| Cost budget, provisional new-identity floor | Implemented in `TrapBridge`; measured only in simulation on one public trace | rounds 17 to 20 |
| Class-conditioned deadlines | Implemented in `TrapBridge` (PR #24); NOT readable by the kernel | tests/test_class_deadlines.py, round 23 parity |
| Heartbeat / stall kill, declared expectation | Simulated only (rounds 24, 26) | no runtime code |
| Live feeding of `TrapBridge` from a running kernel | Not found anywhere | no caller outside tests and experiments |

## 3. Actual control flow today (from inspected code)

```
 kernel (stack-p3-2)                         governance service (experimental, Python)
 -------------------                         -----------------------------------------
 begin_transaction_governed(agent)
   GovernedLimits.effective(agent, deadline)
     HTTP GET /boundaries/stack.agent.<a>.deadline_ns  ---->  Governor.boundaries (hash-chained audit log)
     <-- limit (or error / 404)
     value = min(limit, kernel default)   [cached in kernel MEMORY]
   deadline = now + value
 ... transaction runs ...
 deadline passes -> kernel traps (TrapEvent)
                                |
                                v   (no live path found; replay simulator or a stream)
                          TrapBridge.ingest(trap events), observe_transactions(slice counts)
                                |  policy: pattern, rate, drift, cost budget, new-identity floor
                                v
                          Governor.propose_tightening  ---> audit entry, then limit lowered
 loosening: Governor.request_loosening -> pending -> named operator decision -> grant -> audit
```

Authorization for loosening lives only in the governor's operator registry. The kernel's capability and receipt crates (`stack-issuer`, `stack-receipt`, `stack-admission`) authorize execution, not limit changes, and their worker deadline (`stack-admission`, fixed in its config) is not connected to governed limits.

## 4. Answers to the seam questions

1. Telemetry originates in the kernel's trap events and per-slice completion reports. Nothing runs that path live yet.
2. Restrictions are calculated by `TrapBridge` plus the governor (Python).
3. Restrictions are enforced by the kernel when a transaction begins (absolute deadline = now + governed budget).
4. Authorization enters only in the governor, for loosening.
5. Audit records are written by the governor before a change takes effect.
6. Governor limits are rebuilt by replaying the audit log at start, and startup is refused if the chain is broken or a limit rose without a grant. `TrapBridge` learned state (histories, windows, class observations) is not persisted. The kernel's cache of governed limits is in memory only.
7. Adaptive policy can intervene only by calling the governor's tighten path.
8. The kernel keeps final enforcement authority.

## 5. Findings that shape the design

F1. The kernel has three limit kinds and no tool class. A transaction carries an agent id and a subject digest. Class boundaries exist only in the governor, so no kernel code can read them.
F2. The kernel accepts any governed value of 1 or more. It does not enforce a floor. The governor's 20% usability floor and the bridge's 25% class floor are the only floors, and both are on the service side. A faulty or compromised service could set a one-nanosecond deadline. This is a denial-of-service path the kernel does not check on its own.
F3. On kernel restart with the service unreachable, the governed value is lost and the default applies (looser). The kernel has a file snapshot source that could carry the last values, but the doc does not say it is used for this. Not tested by me.
F4. "Fail closed" is ambiguous in this system. When the adaptive part fails, the safe baseline (static deadline, the kernel default) applies. That is the mission's "fallback integrity". It is not "most restrictive". This proposal uses the baseline meaning and states it.
F5. Every `TrapBridge` change is audited, but decisions that change nothing (held, unknown, below minimum history) are only counted, not logged.
F6. No tool classification service was found. Class names would have to come from the kernel's tool wrapper.
F7. CNS: `experimental` has a CNS connector entry (`src/governance/cns_connector.py`, `cns.gate`). No CNS code evaluates deadlines or limits, and none should. A CNS adapter for this component adds no benefit now and is not proposed.

## 6. Proposed change (needs approval)

Keep the governor as the only store. Add a class dimension to the kernel's read.

Interface changes
- Kernel: `GovernedLimits.effective_for_class(agent, class, default)` returns the lowest of: the class limit (if present and valid), the agent limit, and the default. A transaction may carry an optional tool class; absent or unknown class means today's behavior exactly.
- Kernel (independent validation, answers F2): clamp any governed deadline to [0.25 x default, default]. Out-of-range values are ignored and counted, as above-ceiling values are today. The floor is a kernel constant, not read from the service.
- Kernel (answers F3): optionally write the last good governed values to a local snapshot and read it back at start. Off by default.
- Class id: `stack.agent.<agent>.class.<class>.deadline_ns`. Class names that do not match the safe-id pattern are hashed as agent ids are. Both sides must agree; a shared test vector will pin it.
- Governor/bridge (answers F5): log held and unknown class decisions as audit entries with their inputs (counts, quantile, reason).
- Bridge: persist class observation summaries (not raw durations) into the audit log so a restart does not discard history.

Data flow changes: the kernel reports, per class, the durations of completed calls (it already reports slice quantiles per agent); the bridge calls `refresh_class_deadlines` at slice start. The class label is assigned by the kernel's tool wrapper, never taken from the agent.

Not changed: the whole-agent deadline path, the governor's one-way and operator rules, the circuit breaker, the cost budget.

## 7. Security implications

- Adaptive code never grants authority: it can only call tighten, and the kernel re-validates the value.
- Spoofed class: the class comes from the kernel side, not the agent. Even a wrong class cannot give more than the baseline deadline (the min with agent limit and default) or less than the floor.
- Class flood: capped per agent in the bridge (64 classes).
- A faulty service: bounded by the kernel clamp.
- External signals: declared expectations and heartbeat results stay advisory and tighten-only; they are not part of this change.

## 8. Failure behavior

| Failure | Result |
|---|---|
| Class missing, unknown, or inconsistent | whole-agent deadline applies |
| Class limit missing, malformed, out of range | ignored, counted; agent limit and default apply |
| Service down | last good value in kernel memory; with snapshot enabled, survives restart; otherwise default |
| Adaptive calculation raises | no change; last limits stand; audit notes the error |
| Audit write fails | no change (the governor writes first and does not apply on failure) |
| Corrupt persisted governor state | startup refused (existing behavior) |
| Clock anomaly | deadline uses kernel monotonic/absolute clock as today; bridge does not use wall time for limits |

## 9. Invariant assessment (before any change)

| Invariant | Where | Enforced by | Test today | Weakness today |
|---|---|---|---|---|
| Authority preservation | governor `propose_tightening` | rejects any non-lowering value; sources cannot decide | test_class_deadlines (never loosens), test_signal_interface | none found in governor; kernel does not check direction |
| No autonomous loosening | governor | loosening only via operator decision with grant | test_durable_state, test_red_team_fixes | grant authenticity depends on operator registry; forged-log case documented in DURABLE_STATE.md |
| Fail-closed (baseline meaning) | kernel `effective` | invalid values ignored, default used | Rust tests in governed_limits.rs | restart with service down loses tightening (F3) |
| Auditability | governor audit log | write before apply | test_durable_state | non-changing decisions not logged (F5) |
| Restart safety | governor replay | refuse start on break | test_durable_state | bridge history not persisted; kernel cache not persisted |
| Identity resistance | cost budget, provisional floor | floors on new identities | rounds 19, 20 (simulation) | partial; identity binding not built |
| External-signal isolation | no external signal reaches the governor except source-token tighten/loosen-request | source tokens cannot decide | test_signal_interface | no runtime heartbeat or declaration path exists |
| Enforcement independence | kernel min(default) ceiling only | ceiling | Rust tests | no floor in kernel (F2); proposed fix |
| Bounded adaptation | governor floor 20%, bridge class floor 25% | clamps | test_class_deadlines | floor only on service side today |
| Fallback integrity | whole-agent path unchanged | n/a | existing suite | class code must not alter it; to be proven by comparative arm |

No row is marked PASS: each is "holds in the part I inspected, with the listed gap". After implementation each gets a test, evidence and a result.

## 10. Test plan

Round 23 adversarial suite (new), each case with raw outcomes recorded:
- Identity and class: rotation, spoofed/missing/inconsistent/unseen class, history manipulation (Python bridge tests; class-id agreement test across Python and Rust).
- Deadline: legitimate long calls, mild runaway, gross hang, calls just under the deadline, behavior change after classification, forced permissive and forced restrictive values (the kernel clamp test).
- Governance: loosening without authorization, expired/invalid/replayed grant, external signal asking for relaxation, adaptive component trying to edit policy, bypass attempts (kernel ignores out-of-range class values).
- Reliability: restart mid-adjustment and mid-call, corrupt state, missing telemetry, clock anomaly, audit-write failure, classification failure, calculation failure.
- Comparative arms on the same workloads: (1) whole-agent static baseline, (2) existing adaptive policy (budget with provisional floor), (3) class-conditioned through the kernel path. Metrics: detection, false positives and negatives, time saved on abnormal runs, legitimate disruption, unauthorized relaxation attempts, fail-closed counts, audit completeness, restart consistency. Raw per-agent rows are published, not only aggregates.
- Rust: new tests in `stack-p3-2` for class read, clamp, fallback; end-to-end test against the live Python service (an existing e2e test pattern is ignored-by-default).

Pre-registration: predictions and success criteria will be committed before the run, as in earlier rounds. Round 22's finding stands: timing alone cannot separate a slow honest agent from a mild runaway. This work does not claim to fix that. Extra trusted context is limited to the kernel-assigned class and history; the heartbeat remains a shadow-test question. No machine learning.

## 11. Rollback

Every addition is off unless a class is passed and class boundaries exist. Rollback is to stop passing a class (kernel) and stop calling the class refresh (bridge). Class boundaries left in the governor are inert. The snapshot source is off by default. Nothing in the whole-agent path is edited.

## 12. Acceptance mapping and honest limits

Criteria 1 to 7 and 9 to 12 need the kernel change and the adversarial suite. Criterion 8 (measurable improvement without unacceptable disruption) already has simulation evidence (rounds 21 to 23, about 1 point honest cost; savings 9% to 37% depending on workload) but that is simulation, and the kernel integration will be tested against a real Rust kernel build and a replayed trace, not production traffic. A passing test would not show production readiness, and a working integration would not show the algorithm is optimal.

## 13. Approvals requested

1. Permission to change STACK-Kernel (`stack-p3-2` only). This session currently has read-only access to it; pushing needs a push attachment, which you control.
2. Agreement that the kernel carries a class label supplied by its own tool wrapper.
3. Agreement on the kernel floor constant (0.25 of default).
4. Agreement that the snapshot source (F3) is in scope or deferred.
5. Merge order for the open experimental PRs (#21 to #28) before this branch lands.
