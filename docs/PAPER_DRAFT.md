# Self-Hardening Governance: Asymmetric Adaptive Authority under Adaptive and Generative Adversaries

**Draft manuscript (Phase 12A)**  
**Not yet submitted.** Empirical results from the public prototype at `wking53214/experimental`.

## Abstract

Adaptive systems continuously adjust behavior from internal observations. If those same observations can authorize *relaxation* of safety or resource constraints, the system is exposed to Goodhart pressure, baseline poisoning, and adaptive adversaries. We present a **self-hardening governance** architecture whose central design rule is *asymmetric adaptive authority*: the system may autonomously **tighten** constraints when multi-signal evidence indicates boundary pressure, but **no observation generated inside the adaptive loop may auto-approve loosening or disabling constraints**. Closed-loop evaluation against adaptive and evolutionary adversaries, plus a ten-seed protocol, supports the main empirical claims: diurnal false-positive rates near 1%, high attack detection, near-zero late-window attack success, and sustained usability under long-horizon pressure via a 20% ratchet floor.

## 1. Introduction

Modern infrastructure and AI-adjacent systems adapt continuously. A recurring failure mode is **authority capture by telemetry**: metrics intended as evidence become permission. We separate authority to tighten (automatable) from authority to loosen (human-gated).

## 2. Threat Model

Adaptive adversaries, generative/evolutionary attacks, semantic spoofing, baseline poisoning, Goodhart/metric gaming. Out of scope: Byzantine multi-governor consensus.

## 3. Architecture

Immutable principles; versioned boundaries; event log; multi-signal and hybrid detection; anti-spoof semantic filter; authority cut-point; usability floor at 20% of original limit.

## 4. Evaluation

Multi-seed (N=10): diurnal FP ≈ 1%; attack detection ≈ 96%; late SUCCEEDED = 0; endurance success ≈ 1%; min usability ≈ 21%.

## 5. Limitations

Prototype scale; research-grade adjudication; no general AI alignment claim.

## 6. Related Work

See `references.bib`: Goodhart, runtime verification, adaptive control, autonomic computing, AI governance.

## 7. Conclusion

Asymmetric adaptive authority with hardened multi-signal detection contains adaptive and generative adversaries while preserving human control over constraint relaxation.
