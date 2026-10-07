# Phase 10: Publication Readiness & Operational Hardening

**Date:** October 2, 2026  
**Branch:** main (`wking53214/experimental`)  
**Status:** 📋 Specified — ready to execute  
**Prerequisite:** Phase 9 complete (364/364 tests green)

---

## Context

Phase 9 closed the experimental hardening loop: cyclic-baseline false positives, semantic spoofing, scale-aware detection, usability floor, and a fully green automated suite. Phase 10 shifts from *making the prototype correct* to *making the results defensible and the system operable*.

### Core thesis (unchanged)

Systems may autonomously *tighten* constraints on evidence of boundary pressure. No observation generated inside the adaptive loop may grant authority to *loosen* or *disable* constraints.

---

## Phase 10 Goals

| Track | Goal |
|-------|------|
| **10A** | Multi-seed statistical validation of key claims |
| **10B** | Wire HybridDetectorPipeline as default multi-metric path |
| **10C** | Minimal operator adjudication workflow (LOOSEN / DISABLE) |
| **10D** | Reproducibility package (deps, one-command tests, timing notes) |
| **10E** | arXiv-ready manuscript draft structure + related-work map |

---

## 10A — Multi-Seed Protocol

**Problem:** Single-run green is necessary but not sufficient for publication. FP rates and containment times need means and intervals.

### Design

```
For each experiment E in {Phase7C_diurnal_FP, Phase5_endurance, Phase3_convergence}:
  For seed s in 1..N (N ≥ 10):
    Run E with RNG seed s
    Record metrics
  Report mean ± 95% CI (bootstrap or t-interval)
```

### Metrics

| Experiment | Primary metric | Target |
|------------|----------------|--------|
| 7C legitimate-load immunity | False-positive rate | mean ≤ 5% |
| 7C attack-under-noise | Detection rate | mean ≥ 95% |
| 5 endurance (1000 iter) | Final adversary success rate | mean < 15% |
| 5 endurance | Min usability (% of original) | mean ≥ 20% |
| 3 closed-loop | Late-window SUCCEEDED count | mean ≤ 1 |

### Deliverables

- `scripts/run_multiseed.py` — driver with fixed seed list
- `results/multiseed_summary.json` + markdown table for the report
- pytest marker `@pytest.mark.multiseed` (optional, CI-skippable)

### Exit criteria

- N ≥ 10 seeds completed for all five metrics above  
- No seed violates authority invariants (no auto-LOOSEN)  
- Summary checked into repo under `results/`

---

## 10B — HybridDetectorPipeline Default Path

> **Superseded.** Default changed 2026-10-07: `Governor` now defaults to `detection="generative"`; the hybrid is opt-in (`detection="hybrid"`). Reason: on real 30-metric telemetry the hybrid's traditional layer alarms on about 99.9% of steps (docs/BASELINE_COMPARISON.md).

**Problem:** Phase 9 built hybrid traditional + generative detection; Governor still primarily uses per-boundary AdaptiveAnomalyDetector + pattern path for scalar observations.

### Design

1. When `ingest_metrics(boundary_id, timestamp, metrics_dict)` is used, route through `HybridDetectorPipeline` (already present).
2. When only a scalar `execute_against_boundary` is used, keep AdaptiveAnomalyDetector.
3. Unify proposal generation so both paths share:
   - semantic filter
   - usability floor
   - authority model

### Implementation sequence

1. Add `Governor.detect_from_pipeline(boundary_id)` → proposal or None  
2. Integration tests: multi-metric attack triggers TIGHTEN; expected multi-metric load does not  
3. Document API choice: scalar vs multi-metric entry points  

### Exit criteria

- Hybrid path is the documented default for multi-metric boundaries  
- Existing 364 tests still green (no regression)  
- ≥3 new tests covering hybrid → proposal → apply  

---

## 10C — Operator Adjudication Workflow

**Problem:** LOOSEN / DISABLE require human review, but there is no minimal operator API—only unit tests for adjudication concepts.

### Design (minimal, not a full UI)

```
POST /proposals/{id}/decide
  body: { decision: "approve_loosen" | "reject" | "approve_disable", operator_id, rationale }

GET  /proposals?status=pending_review
GET  /proposals/{id}/evidence   # immutable event + pattern + anomaly pack
```

In-process equivalent for tests:

```python
governor.submit_for_review(proposal)  # marks PENDING_REVIEW
governor.apply_operator_decision(proposal_id, decision, operator_id, rationale)
```

### Invariants

- Operator decisions are append-only in the file store  
- LOOSEN still cannot be auto-approved by any internal score  
- Evidence pack is sufficient for offline audit  

### Exit criteria

- API (or in-process methods) + ≥5 tests for approve/reject LOOSEN and DISABLE  
- Evidence pack includes boundary version chain, recent violations, anomaly scores  
- Authority model tests still prove no auto-LOOSEN  

---

## 10D — Reproducibility Package

### Deliverables

| Artifact | Purpose |
|----------|---------|
| `requirements.txt` or `pyproject.toml` | Pinned `pytest`, `numpy`, etc. |
| `README.md` quick start | Already present; verify one-command path |
| `scripts/run_all_tests.sh` | `python -m pytest tests/ -q` |
| `docs/REPRODUCIBILITY.md` | OS, Python version, hardware class, approx wall time |
| CI workflow (optional) | GitHub Actions: install + pytest on push |

### Exit criteria

- Clean clone → install → full suite green in documented time  
- Python version floor stated (e.g. ≥3.10)  

---

## 10E — Manuscript Structure (arXiv)

Not a full paper in Phase 10—**structure + related-work map + figure list**.

### Suggested outline

1. Introduction — adaptive systems + authority asymmetry  
2. Threat model — adaptive / generative adversaries, Goodhart, spoofing  
3. Architecture — principles, boundaries, events, authority  
4. Detection — multi-signal, multivariate, semantic anti-spoof  
5. Evaluation — Phases 3–9 results; Phase 10A multi-seed tables  
6. Limitations — prototype scale, single-host, adjudication UX  
7. Related work — runtime enforcement, adaptive control, AI governance, Goodhart  
8. Conclusion  

### Related-work anchors (to expand)

- Goodhart’s law / metric gaming  
- Runtime verification and monitor-enforcer patterns  
- Adaptive control and safety envelopes  
- AI governance / scalable oversight (high-level, non-product claims)  

### Exit criteria

- `docs/PAPER_OUTLINE.md` checked in  
- Figure list (architecture diagram, FP table, endurance curve)  
- Citation stubs (BibTeX keys) for core references  

---

## Implementation Sequence

| Order | Track | Depends on | Est. effort |
|-------|-------|------------|-------------|
| 1 | 10D Reproducibility | None | Small |
| 2 | 10B Hybrid default path | Phase 9 code | Medium |
| 3 | 10A Multi-seed | 10D | Medium |
| 4 | 10C Adjudication API | Authority model | Medium |
| 5 | 10E Outline | 10A tables preferred | Small–medium |

---

## Non-Goals (Phase 10)

- Production multi-node consensus / Byzantine governors  
- Learned (ML) baseline discovery beyond current adaptive baselines  
- Full operator UI / SSO / RBAC productization  
- Claims of general AI alignment solution  

---

## Success Definition

Phase 10 is **complete** when:

1. Multi-seed results for core metrics are checked in and meet targets  
2. Hybrid multi-metric path is default and tested  
3. Operator can approve/reject LOOSEN with an audit trail  
4. Clean clone reproduces the green suite  
5. Paper outline + related-work map exist under `docs/`  

Authority invariant remains inviolable throughout.

---

## Relationship to Prior Phases

| Phase | Focus |
|-------|--------|
| 1–2 | Core loop, immutability, semantic layer |
| 3 | Adaptive adversary, closed-loop containment |
| 4–5 | Scale, cascade, endurance |
| 6 | Semantic evasion |
| 7 | Generative adversary, anomaly scoring, adjudication tests |
| 8 | Metrics pipeline, detector integration |
| 9 | Hardening to full suite green (anti-spoof, floor, cyclic FP) |
| **10** | **Statistics, operability, reproducibility, publication prep** |

---

*Phase 10 specification — execute next; does not claim completion until exit criteria above are met.*
