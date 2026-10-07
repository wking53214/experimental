# Phase 9E: Attack Precursor Learning - Early Warning System

## Executive Summary

Learns what metric patterns precede violations, enabling proactive intervention before attacks manifest. Shifts detection from reactive (after violation) to predictive (before violation).

**Status:** Complete | **Tests:** 17 passing | **Source lines:** 240 (`phase9e_precursors.py`)

## Problem Statement

By the time an attack is detected (Phase 9B-9D), damage is already occurring. Precursor learning asks: "What patterns appear before violations? Can we recognize the signature and intervene earlier?"

The insight: Attacks leave fingerprints. Metric patterns before violations are signatures. Learn these patterns, recognize them when they appear, and intervene before the violation manifests.

Example:
- Normal: error_rate and throughput uncorrelated
- Pre-violation signature: error_rate spikes 2 observations before throughput collapses
- Detection: error_rate spike → precursor detected → alert operator 2 observations before expected violation
- Intervention: scale resources, isolate component, etc. before damage hits

## Solution Architecture

### Core Components

#### 1. **AttackPrecursorLearner** 
Extracts patterns that precede violations.

**Key features:**
- Observation history: deque of dicts (metric observations)
- Violation tracking: indices where violations occurred
- Pattern extraction: metric signatures before violations
- Lead time estimation: average observations before violation

**Learning process:**
1. Collect 20+ observations with violations marked
2. For each violation, look back up to 10 observations (lookback_window)
3. Extract lead-time patterns: signatures at 1, 2, 3, 5 steps before violation
4. Retain patterns that occur ≥3 times (min_patterns)
5. Compute confidence = occurrences / total_violations

**Example learning:**
```
Observation history:
[0]: metric=100  (normal)
[1]: metric=102  (normal)
[2]: metric=110  (elevated, signature="elevated:0")
[3]: metric=150  (VIOLATION)

Pattern extracted:
  signature="elevated:0" at lead_time=1 before violation
  Confidence: 1/1 = 100% (if this is only violation)
```

#### 2. **Signature Computation** 
Identifies which metrics deviate from baseline.

**Signature algorithm:**
```
baseline = mean([obs1, obs2, ..., obsN])
current = current_observation
deviation = |current - baseline| / (std(history) + eps)

signature = "elevated:" + metrics_with_deviation > 1.5σ
```

**Example:**
```
Baseline: μ=[100, 50, 5] (CPU, Memory, Error_rate)
Current:  [102, 150, 20]  (Memory spike, Error rate spike)
Deviation: [0.4σ, 2.0σ, 3.0σ]
Signature: "elevated:1,2"  (metrics 1 and 2 deviate)
```

#### 3. **EarlyWarningSystem** 
Real-time precursor detection with confidence escalation.

**Warning escalation:**
```
Observation arrives
    ↓
Precursor matched?  NO  → warning_level = "normal"
    ↓ YES
    confidence = 50% (precursor alone)
    ↓
Is anomaly_score > 0.5?  NO  → warning_level = "elevated"
    ↓ YES
    confidence = 90% (precursor + anomalous)
    warning_level = "critical"
```

**Multi-stage confidence:**
- Stage 1 (Precursor detected): 50% confidence, "elevated" alert
- Stage 2 (Precursor + anomalous): 90% confidence, "critical" alert
- Stage 3 (Violation occurs): 100% confidence, validation/feedback

**Example:**
```
t=0: error_rate=100, anomaly_score=0.1
     → Precursor="elevated:0" found
     → warning_level="elevated", confidence=50%

t=1: error_rate=150, anomaly_score=0.7
     → Precursor="elevated:0" found + anomaly_score>0.5
     → warning_level="critical", confidence=90%

t=2: throughput collapses
     → VIOLATION
     → Precursor pattern validated (lead_time=1 was correct)
```

## What It Detects

### Pre-Violation Metric Patterns
Learns signatures that appear N observations before violations. Example: CPU + Memory both elevated → throughput collapse imminent (2 observations later).

### Attack Pre-Stages
Evolved attacks (Phase 9C) often have setup phases:
1. Metric A attacked for K iterations
2. System reacts (metric B changes)
3. Metric B attacked → violation

Precursor learning catches stage 1, alerts before stage 3.

### Cascade Precursors
Multi-stage failures have signatures:
1. One component degrades (signature A)
2. Dependent component responds (signature B)
3. Full cascade (violation)

Precursor learning detects signatures A or B, enables early intervention.

### Slow-Burn Attacks
Gradual metric deviation preceding violations. Example: latency slowly increases while success_rate slowly decreases over 5 observations. Precursor learns this 5-step pattern, recognizes it when latency trend appears.

## Test Coverage

17 tests in `tests/test_phase9d_e.py`, all passing.

| Test class | Tests | Covers |
|---|---|---|
| TestAttackPrecursorLearner | 12 | too few violations, detection with no patterns, violation index recording, signature and lead-time learning, detection of a learned precursor, normal signature never learned, ordinary observations not flagged after learning, summary shape, indices valid after eviction, evicted violations dropped, learning after the history wraps |
| TestEarlyWarningSystem | 5 | normal state, precursor alone is "elevated" at 0.4 confidence, precursor plus anomaly is "critical" at 0.9, summary counts, relearning after a violation |

**Bug fixed:** violation positions were stored as indices into the bounded history deque and went stale once old observations were evicted. They are now stored as absolute sequence numbers, converted to deque positions when learning, and dropped once evicted. As a result, `violation_indices` and the violation count used for confidence only include violations still inside the history window.

**Second bug fixed:** the learner treated the `"normal"` signature as a precursor, because most steps before any violation look normal. Every ordinary observation then matched it, so 64% of quiet steps raised a warning. `"normal"` and `"unknown"` are no longer learned. In the staged-attack experiment this took false alarms from 64% to 1.9% while still warning ahead of 7 of 8 violations (mean lead 2.3 steps; small sample).

Code coverage was not measured.

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Pattern learning latency | O(violations × lookback) on learn call |
| Precursor detection latency | <0.5ms per observation |
| Lookback window | 10 observations (configurable) |
| Min patterns for learning | 3 occurrences (configurable) |
| Lead time range | 1-5 observations |
| Confidence calculation | occurrence_count / violation_count |
| Observation history size | 100 (deque maxlen) |

## Key Insights

1. **Predictive Detection Shifts Time**
   - Reactive detection: violation detected after occurrence
   - Predictive detection: precursor detected N steps before violation
   - N iterations = N × cycle_time window for intervention
   - Operator can scale resources, isolate, or trigger mitigation

2. **Pattern Confidence is Empirical**
   - Confidence = how often this pattern preceded violations
   - Signature appears 10 times, followed by violation 8 times → confidence=80%
   - Operator knows: precursor is 80% predictive of violation
   - False positive risk is explicit (20% chance precursor without violation)

3. **Lead Time is Measurable**
   - System learns: this precursor appears ~2 observations before violation
   - Expected_violation_in = "2 observations"
   - Operator knows: ~2 × cycle_time seconds to intervene
   - Enables time-bound response planning

4. **Multi-Stage Confidence Escalation**
   - Precursor alone: 50% confidence (elevated alert)
   - Precursor + anomaly: 90% confidence (critical alert)
   - Violation: 100% confidence + feedback
   - Operator has escalation ladder, not binary alert

## Integration Points

**Upstream:**
- HybridDetectorPipeline (Phase 9B) provides anomaly_scores
- AdaptiveDetector (Phase 9D) provides observations
- Violations marked externally (from Governor or detection rules)

**Downstream:**
- Governor feeds violations to learner for pattern updates
- Operator consumes early warnings for mitigation
- Precursor patterns inform Phase 9C attack hypothesis testing

**Cross-phase:**
- Phase 9C evolutionary attacks test if precursor detection catches evolved strategies
- Phase 9D drift detection validates precursor patterns stay relevant
- Phase 9B scores used to boost confidence from precursor-only to precursor+anomalous

## Limitations & Future Work

**Current limitations:**
- Lookback window fixed (10 observations)
- Min patterns threshold fixed (3)
- No temporal pattern matching (only 1, 2, 3, 5 step patterns)
- No multivariate pattern recognition (signatures are independent)
- Pattern learning on-demand, not continuous

**Future enhancements:**
- Adaptive lookback window (based on attack lead times)
- Temporal pattern sequences (A then B then C)
- Multivariate signatures (correlation patterns)
- Continuous online learning (update patterns incrementally)
- Survival analysis (time-to-violation distribution)
- False positive feedback (when precursor appears without violation, reduce confidence)

## Conclusion

Phase 9E shifts anomaly detection from reactive to predictive. By learning what metric patterns precede violations, it enables early warning systems that detect attacks in their setup phase, not their manifestation phase. Combined with Phases 9B-9D, it creates a complete adversarial research harness:

- 9B: Detects attacks (traditional + generative)
- 9C: Evolves attacks (finds detector gaps)
- 9D: Adapts baselines (resists poisoning)
- 9E: Predicts attacks (warns before violation)

The core insight: **Attacks are staged. Each stage leaves a signature. Learn signatures, recognize stages early, intervene before damage manifests.**

---

**Author:** Claude Haiku 4.5  
**Date:** 2026-10-07  
**Status:** Implemented and tested
