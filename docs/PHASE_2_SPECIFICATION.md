# Phase 2 Specification: Semantic Governance Enhancement

**Status:** Planning Document (NOT IMPLEMENTATION)

**Objective:** Transform Phase 1's correct-but-naive governance mechanism into an adaptive system that makes defensible decisions.

**Timeline:** 3-4 weeks

**Effort:** ~1,500-2,000 additional lines of Python

---

## Executive Summary

Phase 1 proved the governance **mechanism** works. Phase 2 must fix the adaptive **strategy**.

**Phase 1 Problem:** Pattern detection is deterministic but blind.
- Counts violations without understanding their meaning
- Cannot distinguish legitimate load from degradation
- Cannot learn from past adaptations
- Cannot recover from bad decisions

**Phase 2 Solution:** Add semantic layer and feedback loops.
- Classify workloads and violations as legitimate or anomalous
- Track service-level objectives (SLOs) as ground truth
- Measure effectiveness before/after adaptation
- Implement automatic rollback for degraded outcomes
- Learn from historical adaptations

**Key Principle:** Phase 2 does NOT redesign Phase 1. It builds on Phase 1's proven foundation (immutability, versioning, authority) and adds intelligence on top.

---

## Phase 1 Gaps to Address

### Gap 1: No Semantic Understanding of Violations

**Problem:**
```
Observed: 3 violations in 30 seconds (CPU > 80%)
Current response: "Pattern detected → tighten boundary to 72%"
Reality: Database migration workload (legitimate), not degradation
Result: System becomes unable to handle necessary operations
```

**Phase 2 Solution: Workload Classification System**

**Components needed:**
1. **WorkloadClassifier**
   - Examines violation context
   - Classifies as EXPECTED, ANOMALOUS, or UNKNOWN
   - Uses heuristics initially, learnable in future

2. **Violation Context Enrichment**
   - Add metadata when violations occur: source, intensity, duration
   - Tag violations with operational context (maintenance window, load test, etc.)
   - Track violation patterns per workload type

3. **Expected Load Registry**
   - Document expected load patterns (batch jobs, deployments, etc.)
   - Store SLA requirements per service
   - Define "legitimate violation" criteria per boundary

**Implementation approach:**
```python
class WorkloadClassifier:
    def classify_violation(self, violation, context) -> ViolationClass:
        # Heuristics:
        # - If violation matches known maintenance window → EXPECTED
        # - If violation source is known batch job → EXPECTED
        # - If violation magnitude > 3x average → ANOMALOUS
        # - Otherwise → UNKNOWN
        pass

class ExpectedLoadRegistry:
    def register_expected_load(self, boundary_id, pattern):
        # Store: "Database backups cause CPU=85% for 10min on Sundays"
        pass
    
    def is_violation_expected(self, violation) -> bool:
        # Check if violation matches known patterns
        pass
```

**Integration with Phase 1:**
- Modify `Governor.execute_against_boundary()` to enrich context
- Pass classification to pattern detector
- Use classification in adaptation proposal reasoning

---

### Gap 2: No Effectiveness Measurement

**Problem:**
```
Adapted: Tightened CPU boundary from 80% to 72%
Did it work? Unknown.
What if it made things worse? Undetectable.
```

**Phase 2 Solution: Pre/Post Adaptation Metrics**

**Components needed:**
1. **Metrics Collector**
   - Track key metrics before adaptation: violation rate, SLO attainment, throughput
   - Track same metrics after adaptation
   - Store time-series data for trending

2. **Effectiveness Oracle**
   - Define what "better" means per boundary
   - Compare pre/post metrics statistically
   - Return clear verdict: IMPROVED, UNCHANGED, DEGRADED

3. **Validation Enhancement**
   - Replace categorical UNKNOWN with data-driven IMPROVED/DEGRADED
   - Track confidence in verdicts
   - Identify ambiguous cases (conflicting metrics)

**Implementation approach:**
```python
class MetricsCollector:
    def record_pre_adaptation_state(self, boundary_id):
        # Snapshot: violation_rate, slo_attainment, throughput, latency_p99
        pass
    
    def record_post_adaptation_state(self, boundary_id, duration=300):
        # Wait for period, then snapshot same metrics
        pass

class EffectivenessOracle:
    def evaluate(self, boundary_id, pre_metrics, post_metrics) -> ValidationOutcome:
        # Logic:
        # - If violation_rate decreased by >20% → IMPROVED
        # - If SLO attainment improved → IMPROVED
        # - If throughput decreased significantly → DEGRADED
        # - Otherwise → UNCHANGED
        return outcome, confidence_score
```

**Integration with Phase 1:**
- Modify `Governor.validate_adaptation()` to use metrics
- Store metrics in validation result for audit
- Track validation accuracy over time

---

### Gap 3: No Rollback for Bad Adaptations

**Problem:**
```
Adaptation made things worse, but system committed to it.
No automatic recovery. Manual intervention required.
```

**Phase 2 Solution: Automatic Rollback Mechanism**

**Components needed:**
1. **Rollback Trigger**
   - If post-adaptation validation returns DEGRADED
   - If SLO violations increase after adaptation
   - If effectiveness confidence is low

2. **Rollback Executor**
   - Revert to previous boundary version
   - Create rollback proposal (auto-approved as TIGHTEN reversal)
   - Record decision and reason

3. **Rollback Prevention**
   - Don't rollback twice for same failure
   - Require N adaptations before declaring strategy failure
   - Escalate to human after repeated failures

**Implementation approach:**
```python
class RollbackManager:
    def should_rollback(self, validation_result) -> bool:
        if validation_result.outcome == ValidationOutcome.DEGRADED:
            return True
        if slo_attainment_decreased(validation_result):
            return True
        return False
    
    def execute_rollback(self, proposal, governor) -> BoundaryVersion:
        # Get previous boundary version
        previous = governor.boundaries.get_boundary_history(
            proposal.boundary_id
        ).get_version(current_version - 1)
        
        # Revert to previous limit
        reverted = governor.boundaries.update_boundary(
            proposal.boundary_id,
            previous.current_limit,
        )
        
        # Record rollback decision
        governor.decisions.record_rollback(proposal, reverted)
        
        return reverted
```

**Integration with Phase 1:**
- Call from `Governor.validate_adaptation()`
- Use same authority model (rollback as TIGHTEN of relaxed boundary)
- Add rollback history to audit trail

---

### Gap 4: No Learning from Adaptations

**Problem:**
```
Made same mistake 3 times:
- Initial boundary too conservative
- Pattern detected, tightened
- Violated again, tightened again
- Violated again, tightened again

No learning. No improvement.
```

**Phase 2 Solution: Adaptation Learning System**

**Components needed:**
1. **Adaptation History Analyzer**
   - Track: boundary → violations → adaptation → outcome
   - Identify patterns in failures
   - Detect repeated mistakes

2. **Learned Thresholds**
   - Adjust pattern detection thresholds based on accuracy
   - If pattern frequently false-positive, raise threshold
   - If pattern frequently missed violations, lower threshold

3. **Adaptation Strategy Selector**
   - Different strategies for different boundary types
   - Learn which tightening magnitude works best per boundary
   - Personalize to specific resource characteristics

**Implementation approach:**
```python
class AdaptationHistoryAnalyzer:
    def analyze_adaptation_effectiveness(self, boundary_id):
        # Get all adaptations for this boundary
        # For each: did violations decrease?
        # Aggregate: what works, what doesn't
        return effectiveness_summary

class LearnedPatternDetector(PatternDetector):
    def adjust_thresholds(self, boundary_id):
        # Get historical accuracy
        history = analyzer.analyze_adaptation_effectiveness(boundary_id)
        
        if history.false_positive_rate > 30%:
            self.patterns[boundary_id].violation_threshold += 1
        elif history.false_negative_rate > 20%:
            self.patterns[boundary_id].violation_threshold -= 1
        
        # Update pattern definition
        pass
```

**Integration with Phase 1:**
- Operate on proposal and validation history
- Periodically re-tune pattern detectors
- Report tuning decisions for audit

---

### Gap 5: No Distributed Coordination

**Problem:**
```
Phase 1 is single-process, single-threaded.
Production requires:
- Multiple governors running independently
- Consistent boundary state
- Distributed event ordering
```

**Phase 2 Solution: Distributed State Machine (Optional for MVP)**

**Note:** This is lower priority than semantic understanding. Can be deferred to Phase 2.2 or 3.

**Approach:**
- Use event streaming (Kafka) for ordering
- Central state store (Postgres) for boundaries
- Idempotent event processing
- Distributed consensus for authority decisions

**For Phase 2 MVP:** Assume single-process, but design for future distribution.

---

### Gap 6: No Human-in-Loop Integration

**Problem:**
```
LOOSEN and DISABLE proposals require human review.
But there's no way to actually request human input.
No integration with ticketing, escalation, or SLA tracking.
```

**Phase 2 Solution: Human Decision Integration**

**Components needed:**
1. **Decision Requester**
   - Create ticket/issue when human review needed
   - Include: proposal details, metrics, risk assessment

2. **Decision Handler**
   - Poll for human decision on outstanding proposals
   - Apply decision (approve/reject)
   - Record approval authority

3. **Escalation Manager**
   - If proposal pending > threshold, escalate
   - If repeated rejections, change strategy
   - Track SLA for human response time

**Implementation approach:**
```python
class HumanDecisionRequester:
    def request_review(self, proposal, governor):
        # Create ticket with proposal details
        # Include: why review needed, metrics, context
        # Return ticket ID for tracking
        pass

class DecisionPoller:
    def poll_pending_decisions(self, governor):
        # Check for completed human decisions
        # Apply approved proposals
        # Reject denied proposals
        pass
```

**Integration with Phase 1:**
- Intercept proposals at authority boundary
- Request review for LOOSEN/DISABLE
- Wait for human decision before applying

---

## New Architecture for Phase 2

### Component Additions

```
PHASE 1 (Foundation)          PHASE 2 (Intelligence)
├── Principle Store            ├── Workload Classifier
├── Boundary Manager           ├── Metrics Collector
├── Event Store                ├── Effectiveness Oracle
├── Pattern Detector           ├── Rollback Manager
├── Proposal Generator         ├── Learning Analyzer
├── Authority Model     -----→ ├── Learned Thresholds
├── Validation Framework       ├── Human Decision Handler
├── Governor                   ├── Escalation Manager
└── Tests                      └── Phase 2 Tests
```

### Modified Governor Flow

**Phase 1 Flow:**
```
Execution → Violation → Pattern → Proposal → Authority → Apply → Validate
```

**Phase 2 Flow:**
```
Execution → Enriched Violation → Smart Pattern → Intelligent Proposal → 
Authority → Apply → Comprehensive Metrics → Effectiveness → 
[Rollback?] → Learn → Optimize
```

### Data Structures

**New Phase 2 structures:**

```python
@dataclass
class ViolationContext:
    """Enriched violation metadata."""
    source: str              # Which service/workload
    intensity: float         # Magnitude of violation
    expected: bool          # Known/expected pattern?
    maintenance_window: bool # During maintenance?
    workload_type: str      # Type of load

@dataclass
class AdaptationMetrics:
    """Before/after metrics for effectiveness measurement."""
    boundary_id: str
    timestamp_pre: float
    timestamp_post: float
    metrics_pre: dict      # violation_rate, throughput, latency, etc.
    metrics_post: dict
    effectiveness: ValidationOutcome
    confidence: float

@dataclass
class AdaptationLearning:
    """Learned insights from adaptation history."""
    boundary_id: str
    total_adaptations: int
    successful_count: int
    success_rate: float
    optimal_threshold: int
    optimal_tighten_factor: float
    common_failure_modes: list[str]
```

---

## Phase 2 Implementation Roadmap

### Sprint 1: Semantic Understanding (1 week)

**Goals:**
- Add violation context enrichment
- Implement workload classifier
- Modify pattern detector to use classification

**Deliverables:**
1. `workload.py` — WorkloadClassifier, ExpectedLoadRegistry
2. Modified `event.py` — Add ViolationContext
3. Modified `pattern.py` — Smart pattern detection
4. Tests for workload classification

**Key decisions:**
- How to define "expected" violations? (config file, registry, ML?)
- How to handle UNKNOWN classifications? (treat as anomalous or ignore?)
- What metadata enriches violations? (source, intensity, duration)

---

### Sprint 2: Effectiveness & Metrics (1 week)

**Goals:**
- Collect pre/post adaptation metrics
- Implement effectiveness oracle
- Replace UNKNOWN validation outcomes

**Deliverables:**
1. `metrics.py` — MetricsCollector, EffectivenessOracle
2. Modified `validation.py` — Data-driven validation
3. Modified `governor.py` — Integrate metrics collection
4. Tests for effectiveness measurement

**Key decisions:**
- Which metrics matter per boundary? (violation_rate, SLO, throughput, latency?)
- How long to observe before declaring effectiveness? (30s? 5min?)
- How to handle conflicting metrics? (SLO improved but throughput dropped?)

---

### Sprint 3: Rollback & Recovery (1 week)

**Goals:**
- Implement automatic rollback
- Prevent repeated failures
- Escalate persistent problems

**Deliverables:**
1. `rollback.py` — RollbackManager, RollbackTrigger
2. Modified `governor.py` — Integrate rollback
3. Circuit breaker for repeated failures
4. Tests for rollback scenarios

**Key decisions:**
- When to rollback? (immediately on DEGRADED? after N hours?)
- How to prevent rollback loops? (don't rollback same proposal twice?)
- When to escalate to human? (after 3 failures? after 1 hour?)

---

### Sprint 4: Learning & Human Integration (1 week)

**Goals:**
- Track adaptation effectiveness over time
- Auto-tune pattern thresholds
- Integrate human decision handler

**Deliverables:**
1. `learning.py` — AdaptationHistoryAnalyzer, LearnedThresholds
2. `human_decisions.py` — HumanDecisionRequester, DecisionPoller
3. Modified `governor.py` — Integrate learning and human decisions
4. Comprehensive tests for Phase 2

**Key decisions:**
- How often to re-tune thresholds? (per adaptation? hourly? daily?)
- Which metrics predict adaptation success? (history or external signals?)
- How to prevent over-fitting to past data? (cross-validation? stability checks?)

---

## Success Criteria for Phase 2

### Mechanism (inherited from Phase 1)
- ✓ Versioning still immutable
- ✓ Authority model still prevents auto-loosening
- ✓ Governance principles still protected
- ✓ All Phase 1 tests still pass

### Strategy (new in Phase 2)
- ✓ Semantic understanding: Can distinguish legitimate load from degradation
- ✓ Effectiveness measurement: Post-adaptation validation is data-driven
- ✓ Automatic recovery: Bad adaptations are automatically rolled back
- ✓ Learning: System improves decision quality over time
- ✓ Human integration: LOOSEN/DISABLE decisions can be reviewed

### Test Coverage
- ✓ 30+ new tests for Phase 2 functionality
- ✓ Adversarial tests for Phase 2 robustness
- ✓ Regression tests: Phase 1 still works

### Benchmarks
- ✓ False positive rate < 10%
- ✓ False negative rate < 15%
- ✓ Adaptation effectiveness > 70%
- ✓ Automatic recovery success rate > 80%

---

## Risk Assessment

### High Risk

**Risk 1: Metrics Ambiguity**
- Problem: Multiple metrics might conflict (SLO up, throughput down)
- Impact: Cannot decide if adaptation good or bad
- Mitigation: Define hierarchy of metrics, require consensus

**Risk 2: Learned Parameters Diverging**
- Problem: Over-tuning thresholds makes system brittle
- Impact: Works on historical data but fails on new workloads
- Mitigation: Cross-validation, stability checks, manual oversight

### Medium Risk

**Risk 3: Workload Classification Accuracy**
- Problem: Heuristics may not capture all legitimate patterns
- Impact: Still get false positives
- Mitigation: Start with conservative classification, expand gradually

**Risk 4: Distributed Coordination Deferred**
- Problem: Single-process assumption breaks in production
- Impact: Need re-architecting for Phase 3
- Mitigation: Document assumptions, design for future distribution

### Low Risk

**Risk 5: Human Decision Integration Complexity**
- Problem: Integration with ticketing systems varies
- Impact: Requires customization per deployment
- Mitigation: Build pluggable interface, example implementations

---

## Integration Points with Phase 1

### No Breaking Changes
- Phase 2 builds on Phase 1, doesn't replace it
- All Phase 1 modules remain unchanged
- New Phase 2 modules integrate at clear boundaries

### Integration Points

1. **ViolationEvent enrichment**
   - Phase 1: `record_violation()` creates basic event
   - Phase 2: Enrich with WorkloadContext before storing

2. **PatternDetector enhancement**
   - Phase 1: Counts violations
   - Phase 2: Filters by classification, adjusts thresholds

3. **Validation enhancement**
   - Phase 1: Categorical validation
   - Phase 2: Data-driven effectiveness measurement

4. **Authority extension**
   - Phase 1: TIGHTEN auto-approved
   - Phase 2: LOOSEN/DISABLE can now be auto-rejected based on metrics

5. **Governor orchestration**
   - Phase 1: Basic loop
   - Phase 2: Enhanced loop with metrics, rollback, learning

---

## Phase 2 Exit Criteria

### Mechanism Criteria (inherited from Phase 1)
- [ ] All Phase 1 tests still pass
- [ ] No regression in versioning, authority, or immutability
- [ ] Governor still maintains governance integrity

### Strategy Criteria (new in Phase 2)
- [ ] Workload classification working
- [ ] Effectiveness measurement accurate
- [ ] Automatic rollback functional
- [ ] Learning system adapting thresholds
- [ ] Human decision integration complete

### Quality Criteria
- [ ] 30+ new tests all passing
- [ ] No Phase 1 regressions
- [ ] Code review completed
- [ ] Documentation updated

### Exit Classification (Target)
- Phase 2 exit should be: **READY FOR PRODUCTION** (with caveats)
- Caveats: Single-process only, heuristic-based learning, manual threshold oversight

---

## Timeline & Effort Estimate

**Total effort:** 3-4 weeks, 1 engineer

**Breakdown:**
- Sprint 1 (Semantic): 5 days
- Sprint 2 (Metrics): 5 days
- Sprint 3 (Rollback): 4 days
- Sprint 4 (Learning): 5 days
- Testing & review: 3 days

**Parallelization opportunities:**
- Sprints 2-3 could overlap (metrics and rollback independent)
- Testing can run continuously

**Estimated code additions:** 1,500-2,000 lines

---

## Phase 3 Considerations (Future)

**Phase 3 should address:**

1. **Distributed Coordination**
   - Event streaming (Kafka)
   - Centralized state store (Postgres)
   - Consensus for authority decisions

2. **Advanced Learning**
   - ML-based workload classification
   - Statistical significance testing
   - Anomaly detection algorithms

3. **Production Features**
   - Multi-tenancy
   - Resource quotas per tenant
   - SLA enforcement
   - Monitoring & observability dashboards

4. **Performance**
   - Horizontal scaling
   - Real-time metrics aggregation
   - Sub-second decision latency

---

## Conclusion

Phase 2 transforms Phase 1's correct-but-naive governance into an intelligent, self-learning system that:

1. **Understands context** — Distinguishes legitimate load from degradation
2. **Measures effectiveness** — Knows if adaptations help or hurt
3. **Recovers from failure** — Automatically rolls back bad decisions
4. **Learns and improves** — Tunes thresholds based on experience
5. **Integrates with humans** — Escalates important decisions for review

**The foundation is sound. Phase 2 is about adding intelligence on top.**

---

**Document Status:** Ready for Phase 2 implementation planning
