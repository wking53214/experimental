# Self-Hardening Governance Architecture — Phase 1 Implementation

## Overview

Phase 1 is a research prototype designed to test the feasibility of a self-hardening governance mechanism. The implementation focuses on establishing the core control loop with explicit authority separation and immutable historical records.

## Architecture

### Core State Machine

```
GOVERNANCE PRINCIPLE (immutable)
        ↓
   BOUNDARY (versioned)
        ↓
   EXECUTION EVENT
        ↓
   VIOLATION EVENT (immutable)
        ↓
   PATTERN DETECTION
        ↓
ADAPTATION PROPOSAL (explicit, separate from execution)
        ↓
AUTHORITY EVALUATION
        ├─ TIGHTEN → AUTO-APPROVED
        ├─ LOOSEN → REQUIRES REVIEW
        └─ DISABLE → REQUIRES REVIEW
        ↓
   BOUNDARY UPDATE (creates new version)
        ↓
   POST-ADAPTATION VALIDATION

```

### Components

#### 1. Governance Principles (`principle.py`)

**Purpose:** Define immutable governance rules that the adaptive mechanism cannot modify.

**Key invariants:**
- No automatic loosening
- No automatic disabling
- Governance principles are immutable
- Historical evidence is immutable

**Implementation:**
- Frozen dataclasses prevent modification
- `PrincipleStore` implements write-once semantics
- System invariants are pre-initialized and non-overridable
- `verify_principle_integrity()` checks invariants before adaptation

**Access:** Read-only during governance operation. Principles cannot be changed by adaptation code.

#### 2. Boundaries (`boundary.py`)

**Purpose:** Define adaptive limits on resources or actions. Boundaries can change; historical versions cannot.

**Key properties:**
- Versioned: Each change creates a new version, not an in-place mutation
- Immutable history: Previous versions remain intact for audit
- Status tracking: ACTIVE, SUPERSEDED, DISABLED

**Implementation:**
- `BoundaryVersion` is a frozen dataclass
- `BoundaryHistory` maintains append-only version list
- Updating a boundary creates a new version with parent reference
- Previous version marked SUPERSEDED, not deleted

**Invariant:** A boundary version, once recorded, cannot be modified or deleted.

#### 3. Execution Events (`event.py`)

**Purpose:** Record every execution attempt against a boundary.

**Key properties:**
- Execution ID, boundary ID, observed value, timestamp
- Outcome: COMPLIANT or VIOLATION
- Context: Additional metadata

**Implementation:**
- `ExecutionEvent` is a frozen dataclass
- `EventStore` implements append-only event log
- Executions are never modified after recording
- Violations create separate immutable violation events

**Invariant:** Events are write-once. Once recorded, they cannot be modified.

#### 4. Violation Events (`event.py`)

**Purpose:** Create immutable evidence of boundary violations.

**Key properties:**
- Violation ID, execution ID, boundary version at violation time
- Observed value vs. limit value
- Timestamp
- Original context

**Critical:** Violations store the boundary version that was active when they occurred. Even if the boundary is later updated, the violation still references the original version. This ensures historical accuracy.

**Implementation:**
- Separate from execution events
- Records boundary version (immutable snapshot)
- Never modified after creation
- Queryable by boundary, time window, or ID

**Invariant:** Historical violations always reference the boundary version active at violation time.

#### 5. Pattern Detection (`pattern.py`)

**Purpose:** Deterministic detection of repeated violations.

**Pattern definition (Phase 1):**
- N violations of the same boundary within time window T
- No machine learning, no scoring, no heuristics
- Deterministic: either the pattern is present or it is not

**Implementation:**
- `PatternDefinition` explicitly states threshold and window
- `PatternDetector.detect_pattern()` checks if threshold is met
- Returns the pattern object if detected, None otherwise
- No hidden calculations

**Current limitation:** Does not distinguish legitimate high-load from actual degradation.

#### 6. Adaptation Proposals (`proposal.py`)

**Purpose:** Explicit proposal object, separate from boundary changes.

**Properties:**
- Proposal ID
- Source evidence (list of violation IDs)
- Current vs. proposed values
- Direction: TIGHTEN, LOOSEN, DISABLE
- Status: PENDING → APPROVED/REJECTED → APPLIED
- Immutable record

**Implementation:**
- `AdaptationProposal` is immutable
- Status changes create new proposal objects
- `ProposalStore` maintains complete proposal history
- No automatic application

**Critical:** A proposal is NOT the same as a boundary change. The proposal exists first, is evaluated, approved, then applied.

#### 7. Authority Model (`authority.py`)

**Purpose:** Enforce differential authority for different adaptation directions.

**Authority rules:**
```
TIGHTEN   → CAN auto-approve
LOOSEN    → MUST NOT auto-approve
DISABLE   → MUST NOT auto-approve
```

**Implementation:**
- `AuthorityModel.evaluate_proposal()` is the ONLY code path that grants auto-approval
- Cannot be bypassed through alternate code paths
- `can_auto_approve()` checks direction only
- `verify_no_auto_loosen()` and `verify_no_auto_disable()` audit integrity

**Critical invariant:** The authority model makes it impossible to auto-approve loosening or disabling through any code path.

#### 8. Validation (`validation.py`)

**Purpose:** Record post-adaptation validation results.

**Validation outcome:**
- IMPROVED: Expected condition achieved
- UNCHANGED: No change observed
- DEGRADED: Adaptation made things worse
- UNKNOWN: Cannot determine

**Implementation:**
- Categorical outcomes, not scores
- User provides explicit validator functions
- `ValidationOracle` holds domain-specific validators
- `ValidationStore` records immutable validation results

**Key:** Validation does NOT invent effectiveness metrics. It records what was actually observed.

#### 9. File-Based Immutable Store (`store.py`)

**Purpose:** Demonstrate that events persist as immutable records.

**Implementation:**
- Append-only JSON file store
- One file per event
- Attempting to overwrite raises error
- Verifiable through file system inspection
- No checksums or hashing (Phase 1 simplicity)

**Usage:** Optional, used to demonstrate historical integrity can be verified externally.

#### 10. Governor Orchestrator (`governor.py`)

**Purpose:** Tie all components together and execute the governance loop.

**Responsibilities:**
1. Execute against boundaries and record events
2. Detect patterns and propose adaptations
3. Authorize proposals
4. Apply approved proposals
5. Validate outcomes
6. Verify governance integrity

**Loop execution:**
```python
execution, violation = governor.execute_against_boundary(...)
proposal = governor.detect_and_propose_adaptation(...)
approved, auth_result = governor.authorize_proposal(proposal)
if approved:
    new_boundary = governor.apply_approved_proposal(approved)
    outcome = governor.validate_adaptation(approved, observed_state)
```

## Key Design Decisions

### 1. No Mutation, Only Versioning

Boundaries are never updated in place. Each change creates a new version. This ensures:
- Historical record is preserved
- Previous state can be audited
- Rollback is possible (via version tracking)

### 2. Separate Proposal from Application

A proposal is an explicit object, not an automatic state change. This ensures:
- Authority can evaluate before applying
- Proposals can be rejected or deferred
- Decisions are recorded for audit

### 3. Immutable Event Record

Events are never modified after creation. This ensures:
- Violations are durable evidence
- Historical accuracy is verifiable
- Causality is preserved

### 4. Authority as Non-Bypassable Code Path

Authority enforcement is in a separate module (`authority.py`) with one entry point. This ensures:
- Cannot accidentally auto-approve loosening
- Rules cannot be circumvented
- Auditable decision making

### 5. Governance Principles as Hard Invariants

System principles are frozen dataclasses that cannot be changed. This ensures:
- Adaptive code cannot modify governance rules
- Invariants are checked before adaptation
- Hard fail if principles are violated

## Testability

### Deterministic Pattern Detection

Patterns are explicit and deterministic:
- N violations in T seconds → pattern detected
- No statistical analysis, no ML
- Tests can verify exact behavior

### Immutability Verification

All immutability is testable:
- Frozen dataclasses raise exceptions on modification
- File store raises exceptions on overwrite
- Version history is verifiable through traversal

### Authority Separation

Authority is testable:
- Try to auto-approve LOOSEN → must fail
- Try to auto-approve DISABLE → must fail
- Try to modify principles → must fail

## Known Limitations

See `LIMITATIONS.md` for complete analysis.

### In Summary:
1. **Pattern detection is blind to semantic meaning** — Cannot distinguish legitimate high-load from actual degradation
2. **No feedback about adaptation effectiveness** — Validation is categorical but often UNKNOWN
3. **Cascading tightening on attack** — Sustained violations cause repeated boundary shrinkage
4. **No rollback mechanism** — Cannot automatically revert bad adaptations
5. **Single-threaded, synchronous** — No handling of concurrent execution

## Test Coverage

- **TEST 1:** Normal execution — boundary unchanged
- **TEST 2:** Single violation — evidence recorded, boundary unchanged
- **TEST 3:** Repeated violations → automatic tightening
- **TEST 4:** Loosening proposal → requires human review
- **TEST 5:** Disabling proposal → requires human review
- **TEST 6:** Historical immutability — violations still reference v1 after boundary updates
- **TEST 7:** False-positive scenario — legitimate workload repeatedly violates and gets tightened (KNOWN FAILURE)
- **TEST 8:** Adversarial feedback manipulation — violations can be injected to trigger undesirable tightening (KNOWN FAILURE)
- **TEST 9:** Monotonicity — automatic adaptation only tightens
- **TEST 10:** Governance integrity — principles cannot be modified

## Exit Criteria — SATISFIED

- [x] 1. Complete loop executes end-to-end
- [x] 2. Boundary versions are immutable historically
- [x] 3. Violations become durable evidence
- [x] 4. Repeated violations produce deterministic adaptation proposal
- [x] 5. Tightening can be automatically authorized
- [x] 6. Loosening cannot be automatically authorized
- [x] 7. Disabling cannot be automatically authorized
- [x] 8. Governance principles cannot be modified by adaptive mechanism
- [x] 9. Validation occurs after adaptation
- [x] 10. Adversarial feedback-manipulation experiment executed
- [x] 11. Every known failure mode documented
- [x] 12. Tests cover both intended behavior and governance-violation attempts

## File Structure

```
/home/user/experimental/
├── src/governance/
│   ├── __init__.py
│   ├── principle.py         # Immutable governance rules
│   ├── boundary.py          # Versioned boundaries
│   ├── event.py             # Execution and violation events
│   ├── pattern.py           # Pattern detection
│   ├── proposal.py          # Adaptation proposals
│   ├── authority.py         # Authority enforcement (critical)
│   ├── validation.py        # Post-adaptation validation
│   ├── store.py             # File-based immutable storage
│   └── governor.py          # Orchestrator
├── tests/
│   ├── __init__.py
│   ├── test_governance_integrity.py   # Tests 1-5, 10
│   ├── test_immutability.py           # Tests 6, 9
│   └── test_adversarial_feedback.py   # Tests 7-8
├── examples/
│   └── demo_loop.py         # Full end-to-end demo
└── docs/
    ├── ARCHITECTURE.md      # This file
    ├── LIMITATIONS.md       # Known limitations
    └── FINDINGS.md          # Adversarial test results
```
