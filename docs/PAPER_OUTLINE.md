# Paper Outline: Self-Hardening Governance via Asymmetric Adaptive Authority

**Working title:** *Self-Hardening Governance: Asymmetric Adaptive Authority under Adaptive and Generative Adversaries*

**Status:** Outline for arXiv draft (Phase 11A)

## 1. Introduction

- Adaptive systems continuously change behavior; internal telemetry must not auto-authorize constraint loosening.
- **Claim:** Asymmetric authority (auto-TIGHTEN / human LOOSEN) + multi-signal detection + anti-spoof semantics.
- Contributions: architecture, hybrid detection, closed-loop adversary evaluation, multi-seed validation.

## 2. Threat Model

Adaptive, generative/evolutionary, semantic spoofing, baseline poisoning, Goodhart/metric gaming.

## 3. Architecture

Principles → versioned boundaries → events → pattern/anomaly → proposal → authority → update. TIGHTEN auto-ok; LOOSEN/DISABLE human-only. Usability floor at 20% of original limit.

## 4. Evaluation

Phases 3–10 results; multi-seed table (FP ≈ 1%, detection ≈ 96%, endurance success 1%, min usability ≈ 21%).

## 5. Limitations

Prototype scale; research-grade adjudication; no general alignment claim.

## 6. Related Work

See `references.bib`: Goodhart, runtime verification, adaptive control, autonomic computing, AI governance.

## 7. Conclusion

Asymmetric adaptive authority with hardened detection contains adaptive/generative adversaries while preserving human control over relaxation.
