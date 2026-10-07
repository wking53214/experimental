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

- **422 tests passing, 4 documented expected failures** (Phases 1-12 plus the Phase 9 detection research); CI runs on Python 3.11 and 3.12.
- Multi-seed run (`scripts/run_multiseed.py --seeds 10`): diurnal false positives about 1%, attack detection about 96% (mean 0.955, 95% interval [0.90, 1.01] from a normal approximation that is invalid for rates; the lower end is below the 0.95 target). The "attack" in that run is a single large spike against the single-metric detector, so it is not evidence about adaptive attackers. See [PHASE9_EXPERIMENT_RESULTS.md](PHASE9_EXPERIMENT_RESULTS.md) for evolved-attacker results on the multi-metric detector (partial detection).
- Core invariant: raising a limit requires a single-use grant issued by the authority when a named operator approves, checked against the boundary's actual limit; the integrity check audits stored version history; rollbacks that loosen are gated like any other loosening. An observation-only attacker never raised a limit in fuzzing (`tests/test_invariant_attacks.py`). This guards against bugs and API misuse, not against malicious code in the same process (see `docs/LIMITATIONS.md`).
- Hybrid multi-metric detection is the default; operator LOOSEN adjudication + HTTP demo (see known issues in `docs/LIMITATIONS.md`).
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
| `src/governance/governor.py` | Main loop, hybrid ingest, adjudication API |
| `src/governance/anomaly_detector.py` | Multi-signal adaptive anomaly detection |
| `src/governance/workload.py` | Anti-spoof semantic classification |
| `src/governance/phase9_integration.py` | HybridDetectorPipeline |
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
| 10 | Multi-seed, hybrid default, adjudication, reproducibility |
| 11 | Paper outline, CI, stochastic seeds, HTTP demo |
| 12 | Draft prose, OpenAPI, figures, README polish |

## License

Research prototype, not production-ready.

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 William N. King.
