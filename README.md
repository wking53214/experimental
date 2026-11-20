# Self-Hardening Governance Architecture

[![CI](https://github.com/wking53214/experimental/actions/workflows/ci.yml/badge.svg)](https://github.com/wking53214/experimental/actions/workflows/ci.yml)

Experimental research prototype for **asymmetric adaptive authority** in adaptive systems.

## Role Within ≡TACK

- **What it is.** A research prototype of "asymmetric adaptive authority": a system may tighten its own limits automatically, but only a named human operator may loosen them, through a single-use grant. Not production-ready.
- **Authority matrix.** It implements its own policy model, enforcement of its limits inside the adaptive loop (`src/governance/authority.py`), a hash-chained audit log, and an optional operator registry. It does not authenticate operators beyond that registry, and the research findings (detection rates, false-alarm results) are measurements of this prototype, not guarantees.
- **Does not own.** The STACK kernel's hard preemption (this is the adaptation half of the kernel's P3.2 proposal per `docs/STACK_FIT.md`), DGK (it governs DGK's health limits through `integrations/dgk_governed.py`), or any production deployment (`docs/DEPLOYMENT_CHECKLIST.md` lists what an operator must decide).
- **Depends on.** See `requirements.txt`. DGK and CNS are optional.
- **Depended on by.** UNKNOWN (not inspected here).
- **Fail behavior.** Tightening is automatic and loosening is human-gated by design. One of the repository's own measurements says false alarms alone drove every machine to the breaker cap in its integrated test (Phase 13, `docs/PHASE_13_REPORT.md`), so tightening-only automation can over-restrict. The scorecard now has ten criteria, not six. Run with DGK installed it reports 7 passing and 3 failing (criteria 3, 4 and 10); without DGK it reports 6 passing, 3 failing and criterion 9 not measured (EXECUTED 2026-11-18, `python -m experiments.scorecard`; same counts on 2026-11-19 at `1143060`). The first six were committed before they were scored (commit `63a5e5b`, then `7cc034b`, VERIFIED from git); 7 to 9 were added afterwards as test-based criteria; for criterion 10 the repository's own red-team record (`docs/RED_TEAM.md`, finding P1) says predictions, code and data went into one commit.

## Core thesis

Systems can autonomously *increase* constraints when evidence of boundary pressure emerges, but **no observation generated inside the adaptive loop may grant authority to reduce or disable constraints**.

What the evidence supports, after fourteen phases and twenty-six rounds of experiments: the one-way ratchet holds (automation never loosens, every change is attributable, limits survive restarts and outside signals), and tightening within a fixed budget on enforcement events catches runaways and bounded attackers. What it does not support: that the system can judge on its own *when* a limit should come down. Statistical detection cries wolf on real telemetry and trap counts cannot tell a legitimately slow agent from a mild runaway, so the project claims **bounded autonomy with human-gated loosening and validated tightening** (criterion 10 in [docs/SUCCESS_CRITERIA.md](docs/SUCCESS_CRITERIA.md)), not self-governing limits. Head to head with a two-way adaptive limiter on the same attackers, the ratchet's safety for honest agents comes from its cap and its drift hold, and the one-way rule's own contribution is narrow: an attacker cannot wait out a recovery and get its budget back. See [docs/STATUS.md](docs/STATUS.md).

**Note (STACK-README-01 Phase 2 review, 2026-11-18; re-checked 2026-11-19 at `1143060`).** Two things in the paragraph above are behind the repository's own record, and one could not be matched:

1. Criterion 10, the criterion named for "validated tightening", **fails** on the scorecard. It fails part (d), how fast a wrong tightening is confirmed (54 of 128 within 6 slices, 80% needed), and passes its other parts (`docs/SUCCESS_CRITERIA.md`; EXECUTED 2026-11-18 with `python -m experiments.scorecard`, same result on 2026-11-19 at `1143060`). So validated tightening is the claim under test, not an established result.
2. The last sentence predates rounds 12 to 15, which narrowed it further: `docs/STATUS.md` says the "direction matters" claim is withdrawn; round 14 puts the one-way rule's measured value at about 3 points of hung time on one patient attacker, bought with 254 operator decisions per 500 program-runs instead of none (`docs/TRACE_TEST_PLAN.md`); and round 15 found it worth at most 3.3 points anywhere in its sweep.
3. "Fourteen phases" could not be matched to the repository. The phase index below lists Phases 1 to 12, one Phase 13 report exists (`docs/PHASE_13_REPORT.md`), and no file names a Phase 14 (INSPECTED). The owner can say which count was meant.

## Design Analogy

Modeled after **asymmetric building codes**: the system may *tighten* limits when stress is evidenced; only a human may *loosen* them.

| Code | Meaning |
|------|---------|
| `AuthorityModel` | Sole gate for system auto-approval |
| `TIGHTEN` | Auto-approve allowed |
| `LOOSEN` / `DISABLE` | Human review required |
| `AdaptiveAnomalyDetector` | Multi-signal stress scoring |
| Registered expected patterns | Work permits (anti-spoof) |
| 20% usability floor | Minimum operable capacity |

See [ELEGANT_AUDIT.md](ELEGANT_AUDIT.md) for architectural defects and beautification notes (Elegant framework).

## Status

- **1003 tests passing** with CNS and DGK installed (before the eight secure-mode tests of 2026-11-19; with CNS but without DGK that run gave 965 passed and the DGK file skipped) (Phases 1-12 plus the Phase 9 detection research); CI runs on Python 3.11 and 3.12. The suite has no skips: `conftest.py` fails the run on one. Without CNS and DGK, 800 run, and `scripts/run_all_tests.sh` names the two modules it leaves out (157 CNS connector tests, 46 DGK tests) instead of skipping them. EXECUTED 2026-11-19 at `1143060`, Python 3.12, one fresh environment per run: 800 passed with `requirements.txt` only, through `scripts/run_all_tests.sh`; 1003 passed with CNS v4.0.0 (`requirements-cns.txt`) and DGK `main` (`1b2fdb8`) installed; 0 failed and 0 skipped in both. Not run: CI on GitHub, Python 3.11.
- Multi-seed protocol (`scripts/run_multiseed.py`; reference results at N=200 in `results/multiseed_summary.md`): diurnal false positives 0.9% (95% interval 0.7% to 1.1%); detection of an obvious spike (about 50 standard deviations) 95.3% (93.5% to 96.5%), which is at the 95% target but not shown to clear it; detection of weaker spikes (6 to 12 standard deviations) 84% (81% to 86%); slow ramp 100%. These test the single-metric detector, not the multi-metric one, and not adaptive attackers: see [PHASE9_EXPERIMENT_RESULTS.md](PHASE9_EXPERIMENT_RESULTS.md) for evolved-attacker results (partial detection). The endurance and late-success experiments give identical results on every seed.
- Core invariant: raising a limit requires a single-use grant issued by the authority when a named operator approves, checked against the boundary's actual limit; the integrity check audits stored version history; rollbacks that loosen are gated like any other loosening. An observation-only attacker never raised a limit in fuzzing (`tests/test_invariant_attacks.py`). This guards against bugs and API misuse, not against malicious code in the same process (see `docs/LIMITATIONS.md`).
- Accountability: every authority decision, boundary change and breaker event is written to a hash-chained audit log (`src/governance/audit_log.py`; `Governor(audit_path=...)`, `GET /audit`), and `Governor(operators=OperatorRegistry)` makes a loosening, rollback or breaker acknowledgement require the operator's own credential. The log makes tampering *evident*, not impossible: a full rewrite verifies unless you compare against an anchor stored out of the governor's reach. Operator credentials are shared secrets, not signatures. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) (T8, T9).
- Baseline poisoning (T2) is measured: an attacker present while the baseline is learned can cut later detection sharply (5% contamination: 2 sd detection from 100% to 13%-36%). A baseline check and a robust fit help at light-to-moderate contamination and are blind to heavy contamination and variance inflation; see [docs/BASELINE_POISONING.md](docs/BASELINE_POISONING.md). The generative default has no clean baseline window unless `Governor(baseline_check_at=N)` is used.
- Phase 13 (integrated system test, [docs/PHASE_13_REPORT.md](docs/PHASE_13_REPORT.md)): streaming 12 real machines through the whole closed loop, false alarms alone (2%-12%, median 6%) drove every machine to the breaker cap within 8,000 steps (all unjustified), and to the 20% floor with the breaker off. The system cannot yet run unattended on real many-metric telemetry. Found and fixed: a 30-metric warmup defect and audit-log flooding while held; spoofed expected-load patterns (T4) are fixed only with `strict_patterns=True`.
- Scorecard against pre-registered success criteria ([docs/SUCCESS_CRITERIA.md](docs/SUCCESS_CRITERIA.md); `python -m experiments.scorecard`): 4 of 6 pass, 2 fail at the first scoring on 2026-10-08. Four criteria were added later, so the command now prints ten rows: 7 pass and 3 fail with DGK installed, or 6 pass, 3 fail and 1 not measured without it (EXECUTED 2026-11-18; same counts on 2026-11-19 at `1143060`). The later four: 7 restart never loosens (pass), 8 signals never loosen (pass), 9 stack integrations work (pass with DGK installed), 10 bounded autonomy with validated tightening (**fail**). Of the first six, passing: safety (not falsified within the threat model), detection no worse than a plain baseline (matches it, does not beat it), a 43% cut in admitted attack usage versus a static limit (bounded by the breaker cap; a perfect human does better on sustained attacks), poisoning survivable with the opt-in trimmed fit. Failing: no unjustified tightenings on real telemetry, and responding to real anomalies before the breaker is spent. Limits are durable when `Governor(audit_path=...)` is set (T13 fixed): they are rebuilt from the verified audit log at startup, and a log edited so that its hashes no longer agree stops startup ([docs/DURABLE_STATE.md](docs/DURABLE_STATE.md)). A log cut short, or rewritten in full with fresh hashes, starts normally and brought back the looser limit in a run here, unless `Governor(anchor_store=...)` is set, in which case both were refused (EXECUTED 2026-11-18). An anchor file on the same host under the same user adds nothing (`src/governance/anchor.py`). What is distinctive and what is not: [docs/POSITIONING.md](docs/POSITIONING.md).
- Bring your own monitoring ([docs/INTEGRATION.md](docs/INTEGRATION.md)): `propose_tightening`, `request_loosening` and `boundary_status` (Python, or `POST /signals/tighten`, `POST /signals/loosen-request`, `GET /boundaries/<id>` over HTTP with per-source credentials) let any external monitor drive the limits. It reuses the floor, circuit breaker, cooldown and audit log, supports retry-safe `idempotency_key`s that survive a restart, and cannot raise a limit whatever it is sent. A source credential can do nothing else. A runnable standard-library example is `examples/external_monitor.py`.
- Current status in one page: [docs/STATUS.md](docs/STATUS.md). Fit with the rest of the repository library ([docs/STACK_FIT.md](docs/STACK_FIT.md)): this is the adaptation half of the STACK kernel's P3.2 proposal. Built: `integrations/dgk_governed.py` (DGK's hardcoded health limits become governed boundaries, tested against the real DGK in CI), `integrations/stack_traps.py` (STACK trap events become per-agent tightening signals, with optional rate and drift rules), and a Rust client in STACK-Kernel (`stack-p3-2`) that reads the governed deadline; closed-loop results with simulated durations are in [docs/STACK_CLOSED_LOOP.md](docs/STACK_CLOSED_LOOP.md), and pre-registered trace replays (three synthetic traces through the kernel, then 20 fresh seeds per roster through a Python model of the kernel held to the kernel's own decisions) in [docs/TRACE_TEST_PLAN.md](docs/TRACE_TEST_PLAN.md): the recommended trap policy catches every runaway and every inflate-first attacker on every seed, saves 6-26% of hung time, and still tightens agents whose natural tail sits above the 1% rule, agents slow from their first task, and about 1 in 8 legitimate regressions late and cheaply; it did not meet its own robustness bar. Running DGK's real code with hostile telemetry found real defects (a NaN was committed and corrupted its statistics; infinities and large negatives raised), now fixed in DGK ([integrations/DGK_FINDINGS.md](integrations/DGK_FINDINGS.md)). Not done: token and memory limits are not enforced by the kernel, the C++ headers read nothing, and nothing has run on real traffic.
- Multi-metric detection defaults to the **generative-only** pipeline (`Governor(detection="generative")`); the hybrid is opt-in (`detection="hybrid"`). The default was switched from hybrid because the hybrid does not beat plain baselines and, on real 30-metric telemetry (SMD), its traditional layer alarms on about 99.9% of steps (scaling its rule did not fix it). Even the generative default false-alarms about 7% of steps on unseen real machines, so it is not yet fit to act on unattended: see [docs/BASELINE_COMPARISON.md](docs/BASELINE_COMPARISON.md). Operator LOOSEN adjudication + HTTP demo (see known issues in `docs/LIMITATIONS.md`). Threat model and the tightening denial-of-service result: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
- Draft manuscript under `docs/PAPER_DRAFT.md`

## Quick start

```bash
pip install -r requirements.txt
bash scripts/run_all_tests.sh   # not ./scripts/run_all_tests.sh: the file is not marked executable in git, so that form stops with "Permission denied"

python scripts/run_multiseed.py --seeds 10
python scripts/adjudication_server.py   # http://127.0.0.1:8765
```

**Know the demo server's defaults before you start it (EXECUTED 2026-11-18).** With no token set it listens on 127.0.0.1 only and every endpoint is open: any program on the same machine can create a limit, file a loosening request and approve it under any name it types. In a run here a caller raised a limit from 100 to 500 that way, and the audit log recorded `identity_verified: false`. With one shared `ADJUDICATION_TOKEN`, every holder of that token can decide as any `operator_id`: the same token approved one request as "alice" and the next as "bob". Only `ADJUDICATION_OPERATOR_TOKENS=alice=...,bob=...` ties a decision to one person's own credential (a token used under another name was refused with 403). Listening on all interfaces is refused unless `ALLOW_PUBLIC_BIND=1`, and there is no TLS. **Secure mode.** `ADJUDICATION_SECURE_MODE=1` makes the server refuse to start (exit 2, each missing item named) unless `ADJUDICATION_OPERATOR_TOKENS` (each token long enough for the registry), `ADJUDICATION_AUDIT_LOG` and `ADJUDICATION_ANCHOR_FILE` are set and the anchor is not the audit log's own file. With them set, the server publishes the log's head to the anchor file after every write (`FileAnchorStore`) and refuses to start if the log was rewritten, truncated or deleted (EXECUTED 2026-11-19: a boundary created with Alice's token wrote one audit line and one anchor line; a call with no token was refused). Without the switch the server stays in demo mode and says so at startup. The anchor file only protects the log if it lives where the server's own account cannot rewrite it (`docs/DEPLOYMENT_CHECKLIST.md`). The library is stricter than the demo: `Governor()` with no `OperatorRegistry` refuses every operator decision unless `unverified_operators=True` is passed, which the demo server does when no operator tokens are set.

## Key modules

| Path | Role |
|------|------|
| `src/governance/governor.py` | Main loop, multi-metric ingest (generative by default), adjudication API |
| `src/governance/anomaly_detector.py` | Multi-signal adaptive anomaly detection |
| `src/governance/workload.py` | Anti-spoof semantic classification |
| `src/governance/phase9_integration.py` | HybridDetectorPipeline (opt-in), GenerativePipeline (default) |
| `src/governance/authority.py` | TIGHTEN auto / LOOSEN human-gated |

## Documentation

| Doc | Description |
|-----|-------------|
| [ELEGANT_AUDIT.md](ELEGANT_AUDIT.md) | Elegant framework audit + defects |
| [PHASE_12_COMPLETION_REPORT.md](PHASE_12_COMPLETION_REPORT.md) | Latest phase report |
| [docs/PAPER_DRAFT.md](docs/PAPER_DRAFT.md) | Draft manuscript prose |
| [docs/FIGURES.md](docs/FIGURES.md) | Mermaid architecture diagrams |
| [docs/openapi-adjudication.yaml](docs/openapi-adjudication.yaml) | OpenAPI for demo HTTP API |
| [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) | Environment and commands |
| [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md) | What an operator must decide before this runs anywhere real |
| [docs/TRACE_TEST_PLAN.md](docs/TRACE_TEST_PLAN.md) | Pre-registered trace replays: predictions, results, changes made after seeing results |

## Phase index

| Phase | Focus |
|-------|--------|
| 1–2 | Core loop, immutability, semantic layer |
| 3 | Adaptive adversary, closed-loop containment |
| 4–5 | Scale, cascade, endurance |
| 6 | Semantic evasion |
| 7–8 | Generative adversary, anomaly scoring, metrics pipeline |
| 9 | Hardening to full suite green |
| 10 | Multi-seed, hybrid default (since changed to generative), adjudication, reproducibility |
| 11 | Paper outline, CI, stochastic seeds, HTTP demo |
| 12 | Draft prose, OpenAPI, figures, README polish |

## Optional CNS connection

This repository does not need CNS. It has no runtime dependency, nothing in it imports CNS when it loads, and `tests/test_cns_independence.py` checks this with CNS blocked. `src/governance/cns_connector.py` is the one place that knows CNS exists. Install it with `pip install -r requirements-cns.txt` (pins CNS v4.0.0); without CNS, asking for a digest raises `CnsNotInstalled` and nothing else changes.

It makes three records **bindable** with `cns.gate`: a limit state (`BoundaryVersion`), a limit change (the limit before and after, the direction those imply, and who or what asked) and an `AuthorizationDecision` (who decided and how). A CNS verdict then carries a `subject_digest`, and `binds(result, record)` says whether that verdict was issued against exactly that record.

**Binding records what a limit was. It grants nothing.** The thesis above is untouched: automation may only tighten, and only a named operator, through a grant from the `AuthorityModel`, may loosen. The connector only reads records; it never issues or consumes a grant and never changes a limit (a test scans it for exactly that), and a passing verdict bound to a record of a raise does not raise anything. `created_at` and the decision timestamp are left out of the bound content, because a restart rebuilds them from the audit log and a limit must keep its digest across a restart. Free-text reasons are bound as SHA-256 digests and never copied.

## License

Apache-2.0 (see `LICENSE` and `NOTICE`). Research prototype, not production-ready.

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 William N. King.
