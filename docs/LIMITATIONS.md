# Phase 1 Known Limitations

This document catalogs all known limitations and failure modes discovered during Phase 1 research. These are NOT treated as bugs to be fixed, but as FINDINGS to inform Phase 2 design.

## Critical Limitations

### 1. Pattern Detection Cannot Distinguish Legitimate Load from Degradation

**Issue:** The pattern detector is completely blind to the semantic meaning of violations.

**Scenario:**
- System has a conservative initial boundary: `max_cpu = 60%`
- A legitimate, critical workload consistently requires `80% CPU`
- This is legitimate and necessary, not a failure
- Pattern detector sees "3 violations in 30s" and proposes tightening
- Boundary shrinks to `54%`, making the legitimate work impossible

**Root Cause:** Phase 1 has no semantic understanding of:
- Workload validity
- Business requirements
- Expected vs. unexpected load

**Evidence:** TEST 7 demonstrates this directly.

**Phase 2 Solution:** Would require:
- Workload classification (ML or domain-specific rules)
- Service-level objectives (SLOs) as constraints
- Feedback from load generators or traffic patterns
- Semantic understanding of "legitimate" vs. "degraded"

**Current Workaround:** None. This is a fundamental limitation of the deterministic pattern detector.

---

### 2. Cascading Boundary Shrinkage Under Sustained Violation

**Issue:** Repeated violation patterns trigger repeated tightening, eventually making the boundary unusable.

**Scenario:**
```
Round 1: Boundary = 100, violations → tighten to 90
Round 2: Boundary = 90, violations → tighten to 81
Round 3: Boundary = 81, violations → tighten to 72.9
...
Round 5: Boundary = 59.0 (completely restricted)
```

**Root Cause:**
- No "damping" between tightenings
- No learning from past adaptations
- Pattern always triggers if threshold is met
- Each iteration reduces the boundary by 10%

**Evidence:** TEST 8 demonstrates adversarial injection causing cascading tightening.

**Phase 2 Solution:** Would require:
- Adaptation history analysis ("have we already tightened this boundary recently?")
- Dampening: Don't tighten twice for the same pattern
- Circuit breaker: After N tightenings, require human review
- Rollback detection: If violations don't decrease after adaptation, try looser boundary

**Current Workaround:** Manually reset boundaries. No automatic recovery.

---

### 3. Adversarial Feedback Manipulation

**Issue:** An attacker can inject violations to trigger undesirable adaptations.

**Attack vector:**
- Attacker observes the pattern detection rules (N violations in T seconds)
- Attacker injects exactly N violations to trigger the pattern
- Attacker may target irrelevant boundaries to cause collateral damage

**Variants:**
- **Noise injection:** Generate sub-threshold violations to keep the system in a constant "near-threshold" state
- **Coordinated attack:** Violate multiple boundaries simultaneously to trigger cascading tightening
- **Sustained barrage:** Continuous violation injection to force the system into increasingly restricted state

**Evidence:** TEST 8 demonstrates multiple attack vectors.

**Phase 2 Solution:** Would require:
- Source tracing: Track whether violations come from expected sources
- Anomaly detection: Detect unusual violation patterns (e.g., identical values)
- Rate limiting: Reject violations at suspicious frequencies
- Human review thresholds: After N auto-approvals, require manual review

**Current Workaround:** None. The system has no defenses against adversarial input.

---

### 4. No Automatic Rollback or Recovery

**Issue:** Once a boundary is tightened incorrectly, there is no automatic way to revert it.

**Scenario:**
- Boundary is tightened due to false-positive violations
- System now rejects legitimate work
- Administrator has not monitored yet
- No automatic recovery mechanism kicks in

**Root Cause:** Phase 1 has no:
- Version-based rollback logic
- Automatic "undo" decision
- Metrics to detect bad adaptations
- Threshold-based reversion

**Phase 2 Solution:** Would require:
- Effectiveness measurement: Track metrics before/after adaptation
- Automatic reversion if effectiveness degrades
- Human-requested rollback with version tracking
- A/B testing of boundary changes

**Current Workaround:** Manual reset. Administrator must notice and intervene.

---

### 5. Validation is Often Meaningless

**Issue:** Post-adaptation validation typically returns UNKNOWN because there is no domain-specific oracle.

**Scenario:**
- Boundary is tightened
- Validator function is called
- Function has no context about what "good" looks like
- Returns UNKNOWN
- Decision is recorded but provides no actionable insight

**Root Cause:**
- Phase 1 requires explicit validator functions
- There is no generic "effectiveness" metric
- Without domain knowledge, validation cannot meaningfully judge outcome

**Phase 2 Solution:**
- Pre-define SLOs and success metrics
- Track key metrics (latency, throughput, error rate) before/after
- Use SLO attainment as validation oracle
- Automatic rollback if SLO violated

**Current Workaround:** Supply custom validator functions for each boundary. Otherwise, validation is decorative.

---

### 6. No Handling of Concurrent Execution

**Issue:** Governor is single-threaded and synchronous. Real systems are concurrent.

**Problem scenarios:**
- Multiple execution sources generating violations simultaneously
- Boundary being updated while executions are in flight
- Time-window detection races

**Impact:** In production, timing could cause:
- Missed violations
- Race conditions in pattern detection
- Inconsistent history

**Phase 2 Solution:**
- Event ordering guarantees
- Concurrent pattern detection with locks
- Transaction-like semantics for boundary updates
- Message queue-based architecture (Kafka/Flink mentioned in original spec)

**Current Workaround:** Single-threaded operation only. Production use would require thread safety.

---

### 7. No Adaptation Efficacy Learning

**Issue:** The system does not learn whether past adaptations actually helped.

**Scenario:**
- Pattern detected, boundary tightened
- Violations continue
- No detection that the adaptation failed
- Next pattern triggers another tightening

**Root Cause:**
- No comparison of violation rates before/after adaptation
- No statistical testing for effectiveness
- Validation is isolated from next adaptation decision

**Phase 2 Solution:**
- Continuous metric tracking
- Statistical comparison of violation distributions before/after
- Feedback loop: "Did the adaptation reduce violations?"
- Adaptive strategy selection based on historical effectiveness

**Current Workaround:** Manual analysis. No automatic learning.

---

## Design Limitations

### 8. Pattern Definition is Hard-Coded

**Issue:** Pattern threshold (N violations in T seconds) is global and fixed.

**Problem:**
- Some boundaries may need more sensitive patterns
- Others may need less sensitive patterns
- One-size-fits-all approach is rigid

**Phase 2 Solution:**
- Per-boundary pattern definitions
- Learned pattern thresholds based on historical data
- Semantic categorization of violation types

**Current Workaround:** Manually tune pattern parameters per boundary.

---

### 9. Tightening Magnitude is Fixed

**Issue:** Every tightening multiplies the boundary by 0.9 (hardcoded).

**Problem:**
- May be too aggressive for some boundaries
- May be too conservative for others
- No adaptation based on severity of violations

**Phase 2 Solution:**
- Adaptive tightening: larger violations trigger larger tightening
- Historical trend analysis
- Learned tightening factors

**Current Workaround:** Manually set initial boundaries very conservatively.

---

### 10. No Human-in-Loop Mechanism

**Issue:** LOOSEN and DISABLE require human review, but there is no mechanism to request or track human decisions.

**Problem:**
- Proposals are generated and marked as "requires human review"
- No way to actually get human input
- No timeout or escalation if human doesn't respond

**Phase 2 Solution:**
- Integration with ticketing system (Jira, Linear)
- Timeout and escalation for unreviewed proposals
- Decision tracking and audit trail
- Human review analytics

**Current Workaround:** Manual proposal review. No automated integration.

---

## Architectural Limitations

### 11. No Distributed Coordination

**Issue:** Phase 1 assumes single-process execution.

**Problem in production:**
- Multiple governors running independently would have conflicting views
- Distributed boundaries would be inconsistent
- Events could be lost

**Phase 2 Solution:** (Original spec mentioned)
- Kafka for event streaming
- Flink for distributed processing
- Postgres for centralized state
- Consistent boundary versioning across processes

---

### 12. No Persistence Across Restarts

**Issue:** Governor state is in-memory. Restart loses all history.

**Problem:**
- Can inspect historical events during runtime
- Cannot query history after process restart

**Phase 2 Solution:**
- Database backing (Postgres)
- Event log persistence
- Durable state machine

**Current Workaround:** Keep process running or use file store (which is separate from in-memory state).

---

### 13. No Support for Hierarchy or Groups

**Issue:** Each boundary is independent. No way to group or relate them.

**Problem:**
- Cannot say "if A tightens, B should also consider tightening"
- No dependency graph
- No policy about related boundaries

**Phase 2 Solution:**
- Boundary groups with coordinated policies
- Hierarchical boundaries (parent/child)
- Correlated pattern detection

---

## Test Findings

### TEST 7: False-Positive Scenario — FAILS (EXPECTED)

**Result:** Governor blindly tightens boundary despite legitimate workload.

**Finding:** The deterministic pattern detector has no way to distinguish:
- Legitimate high-load from degradation
- Expected spikes from unexpected failures
- Valid violations from noise

**Impact:** Conservative initial boundaries will be progressively restricted, breaking legitimate work.

**Documented:** FINDINGS.md

---

### TEST 8: Adversarial Feedback Manipulation — FAILS (EXPECTED)

**Result:** Sustained violation injection triggers repeated tightening.

**Finding:** No defense against adversarial input. Pattern detection can be weaponized to degrade system constraints.

**Impact:** Hostile actor can drive system into unusable state.

**Documented:** FINDINGS.md

---

## Assessment

### What Phase 1 Successfully Proves

- ✓ Governance principles CAN be immutable
- ✓ Boundary versions CAN be preserved historically
- ✓ Violations CAN be durable evidence
- ✓ Adaptation proposals CAN be separate from execution
- ✓ Authority CAN prevent automatic loosening/disabling
- ✓ Validation CAN be recorded and audited

### What Phase 1 Reveals as Infeasible Without Further Work

- ✗ Blind pattern detection is exploitable
- ✗ Cascading tightening without dampening is unstable
- ✗ No semantic understanding = no effective adaptation
- ✗ No feedback loop = no learning
- ✗ Manual recovery is required

### Recommendation

Phase 1 validates the **governance mechanism** (principles, versioning, authority separation). However, it exposes that **adaptive strategy selection** (what to tighten, when, how much) requires semantic understanding and feedback loops that Phase 1 deliberately avoided.

Phase 2 should focus on:
1. Workload classification and SLO tracking
2. Effectiveness measurement and learning
3. Rollback and recovery mechanisms
4. Distributed coordination
5. Human-in-loop integration

The core governance architecture is sound. The challenge is in making adaptations that are actually beneficial.


## Issues found by running the code (October 2026)

**Core invariant**: fixed on 2026-10-07. Raising a limit now needs a single-use `AuthorizationGrant` that only `AuthorityModel` issues (when a named operator approves), checked in `BoundaryStore.update_boundary` against the boundary's actual current limit. The authority takes the stricter of the declared and value-implied direction; `apply_approved_proposal` requires a recorded authority decision and enforces the 20% floor; the integrity check audits the stored version history for any upward step without a grant; and a rollback that would raise a limit is queued for human review unless a named operator initiates it (`RollbackExecutor.execute_rollback(..., operator_id=...)`). Regression tests are in `tests/test_invariant_attacks.py`. Scope: this stops bugs and misuse of the API. Python cannot stop malicious code in the same process from forging a grant or editing the stored history; the audit makes such edits visible, not impossible. Consequence of the thesis, not a bug: an attacker who can generate violations can drive limits to the 20% floor, and only a human can restore them (the endurance experiment ends every seed at 20.6% usability).

**HTTP adjudication demo (`scripts/adjudication_server.py`)**: fixed on 2026-10-07: bad JSON, missing fields, non-finite or non-positive numbers, unknown IDs, duplicate boundaries and oversized bodies now return JSON 4xx errors; unexpected errors return a JSON 500; GET endpoints need the token (only a minimal `/health` is open); token comparison is constant-time; per-operator tokens (`ADJUDICATION_OPERATOR_TOKENS`) bind the operator identity to the credential. Remaining: with a single shared token, `operator_id` is still self-asserted (the response says `identity_verified: false`); there is no TLS, rate limiting or persistence (state is one in-memory Governor); it is a demo, not a deployable service.

**Multi-seed protocol (`scripts/run_multiseed.py`)**: fixed on 2026-10-07. Rates are pooled across seeds with a Wilson interval (the old normal approximation produced an interval above 1.0); continuous values use a t interval; results that are identical on every seed are flagged; each target reports whether the point estimate meets it and whether the 95% interval supports it (`--strict` exits non-zero unless every interval does; CI uses the default, which only checks point estimates, because N=3 cannot support anything); two harder informational scenarios were added (weaker spikes, slow ramp). Remaining: `7c_attack_detection` uses spikes about 50 standard deviations above baseline, so it is obvious by construction (and still only reaches 95.3%, interval 93.5% to 96.5%, so the 95% target is met by the point estimate but not supported by the interval); all scenarios test the single-metric detector, not the multi-metric hybrid or adaptive attackers; the endurance and late-success experiments are seed-independent (their randomness never changes the outcome), so more seeds add nothing for them.


**Detection default**: changed on 2026-10-07 from the hybrid to the generative-only pipeline. The hybrid does not beat plain Mahalanobis or z-score baselines at matched false-alarm rates, and on real 30-metric telemetry (Server Machine Dataset) its traditional layer alarms on about 99.9% of steps. The generative default still false-alarms about 7% of steps on 8 unseen real machines (median 7%), so alarms should not trigger unattended action on real many-metric data without the circuit breaker in `docs/THREAT_MODEL.md` (T3). Details: `docs/BASELINE_COMPARISON.md`.
