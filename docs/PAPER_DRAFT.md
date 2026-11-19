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

Immutable principles; versioned boundaries; event log; multi-signal detection (hybrid is opt-in; the default is generative-only after the real-data comparison in `docs/BASELINE_COMPARISON.md`); anti-spoof semantic filter; authority cut-point; usability floor at 20% of original limit.

## 4. Evaluation

Multi-seed (N=10): diurnal FP ≈ 1%; attack detection ≈ 96%; late SUCCEEDED = 0; endurance success ≈ 1%; min usability ≈ 21%.

## 5. Limitations

Prototype scale; research-grade adjudication; no general AI alignment claim.

## 6. Related Work

**One-way authority is old; the contribution is measuring it.** Process-level primitives already let authority shrink and never grow:
OpenBSD `pledge` and `unveil` (`openbsd2015pledge`), Linux `seccomp` with `no_new_privs` (`linux2012seccomp`), and attenuating
credentials such as macaroons (`birgisson2014macaroons`). Tighten-only control of AI agents has also been proposed, with
monotonic restriction as a stated property (`marin2026governing`). This work does not claim the direction. It applies it to
adaptive numeric limits (deadline, tokens, memory) and measures what the direction is worth.

**Adaptive limits.** Production systems adapt limits in both directions: AIMD (`chiu1989aimd`), Netflix's concurrency-limits
(`netflix2018concurrency`) and GitLab's Gitaly limit (`gitlab2023gitaly`). Rounds 9 to 16 compare against this family head to
head and find that the breaker cap and the interpretation rules do most of the work; the direction is worth at most 3.3 points
of hung time, and on real tool-call latencies the two-way limiter's honest-cost advantage did not reproduce.

**Validating an action by a held-back canary.** Canary analysis (`netflix2018kayenta`) judges a release against a baseline;
challenge insertion (`rehak2009challenge`) hides known inputs from an adaptive detector. The validation layer applies the
comparison to a governance action and uses a keyed selection for the same reason as the hidden challenges.

**Bounded automation.** Runtime assurance wraps an unverified controller in a verified envelope (`sha2001simplex`). The
worst-case certificate (`docs/CERTIFICATE.md`) is the analogue here: the cap fixes in advance what the limiter can cost an honest
agent and what it can save from a runaway. Background: `goodhart1975`, `manheim2018`, `aastrom2013`, `kephart2003`.

## 7. Conclusion

Asymmetric adaptive authority with hardened multi-signal detection contains adaptive and generative adversaries while preserving human control over constraint relaxation.
