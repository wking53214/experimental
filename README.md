# Self-Hardening Governance Architecture

[![CI](https://github.com/wking53214/experimental/actions/workflows/ci.yml/badge.svg)](https://github.com/wking53214/experimental/actions/workflows/ci.yml)

Experimental research prototype for **asymmetric adaptive authority** in adaptive systems.

## Core thesis

Systems can autonomously *increase* constraints when evidence of boundary pressure emerges, but **no observation generated inside the adaptive loop may grant authority to reduce or disable constraints**.

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

- **584 tests passing** (Phases 1-12 plus the Phase 9 detection research); CI runs on Python 3.11 and 3.12.
- Multi-seed protocol (`scripts/run_multiseed.py`; reference results at N=200 in `results/multiseed_summary.md`): diurnal false positives 0.9% (95% interval 0.7% to 1.1%); detection of an obvious spike (about 50 standard deviations) 95.3% (93.5% to 96.5%), which is at the 95% target but not shown to clear it; detection of weaker spikes (6 to 12 standard deviations) 84% (81% to 86%); slow ramp 100%. These test the single-metric detector, not the multi-metric one, and not adaptive attackers: see [PHASE9_EXPERIMENT_RESULTS.md](PHASE9_EXPERIMENT_RESULTS.md) for evolved-attacker results (partial detection). The endurance and late-success experiments give identical results on every seed.
- Core invariant: raising a limit requires a single-use grant issued by the authority when a named operator approves, checked against the boundary's actual limit; the integrity check audits stored version history; rollbacks that loosen are gated like any other loosening. An observation-only attacker never raised a limit in fuzzing (`tests/test_invariant_attacks.py`). This guards against bugs and API misuse, not against malicious code in the same process (see `docs/LIMITATIONS.md`).
- Accountability: every authority decision, boundary change and breaker event is written to a hash-chained audit log (`src/governance/audit_log.py`; `Governor(audit_path=...)`, `GET /audit`), and `Governor(operators=OperatorRegistry)` makes a loosening, rollback or breaker acknowledgement require the operator's own credential. The log makes tampering *evident*, not impossible: a full rewrite verifies unless you compare against an anchor stored out of the governor's reach. Operator credentials are shared secrets, not signatures. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) (T8, T9).
- Baseline poisoning (T2) is measured: an attacker present while the baseline is learned can cut later detection sharply (5% contamination: 2 sd detection from 100% to 13%-36%). A baseline check and a robust fit help at light-to-moderate contamination and are blind to heavy contamination and variance inflation; see [docs/BASELINE_POISONING.md](docs/BASELINE_POISONING.md). The generative default has no clean baseline window unless `Governor(baseline_check_at=N)` is used.
- Phase 13 (integrated system test, [docs/PHASE_13_REPORT.md](docs/PHASE_13_REPORT.md)): streaming 12 real machines through the whole closed loop, false alarms alone (2%-12%, median 6%) drove every machine to the breaker cap within 8,000 steps (all unjustified), and to the 20% floor with the breaker off. The system cannot yet run unattended on real many-metric telemetry. Found and fixed: a 30-metric warmup defect and audit-log flooding while held; spoofed expected-load patterns (T4) are fixed only with `strict_patterns=True`.
- Scorecard against pre-registered success criteria ([docs/SUCCESS_CRITERIA.md](docs/SUCCESS_CRITERIA.md); `python -m experiments.scorecard`): 4 of 6 pass, 2 fail. Passing: safety (not falsified within the threat model), detection no worse than a plain baseline (matches it, does not beat it), a 43% cut in admitted attack usage versus a static limit (bounded by the breaker cap; a perfect human does better on sustained attacks), poisoning survivable with the opt-in trimmed fit. Failing: no unjustified tightenings on real telemetry, and responding to real anomalies before the breaker is spent. Limits are durable when `Governor(audit_path=...)` is set (T13 fixed): they are rebuilt from the verified audit log at startup, and a tampered log stops startup ([docs/DURABLE_STATE.md](docs/DURABLE_STATE.md)). What is distinctive and what is not: [docs/POSITIONING.md](docs/POSITIONING.md).
- Multi-metric detection defaults to the **generative-only** pipeline (`Governor(detection="generative")`); the hybrid is opt-in (`detection="hybrid"`). The default was switched from hybrid because the hybrid does not beat plain baselines and, on real 30-metric telemetry (SMD), its traditional layer alarms on about 99.9% of steps (scaling its rule did not fix it). Even the generative default false-alarms about 7% of steps on unseen real machines, so it is not yet fit to act on unattended: see [docs/BASELINE_COMPARISON.md](docs/BASELINE_COMPARISON.md). Operator LOOSEN adjudication + HTTP demo (see known issues in `docs/LIMITATIONS.md`). Threat model and the tightening denial-of-service result: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
- Draft manuscript under `docs/PAPER_DRAFT.md`

## Quick start

```bash
pip install -r requirements.txt
./scripts/run_all_tests.sh

python scripts/run_multiseed.py --seeds 10
python scripts/adjudication_server.py   # http://127.0.0.1:8765
```

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

## License

Research prototype, not production-ready.

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 William N. King.
