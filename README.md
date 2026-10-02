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

- **375 tests passing** (Phases 1–12); CI green on `main`
- Multi-seed validation (N=10): diurnal FP ≈ 1%, attack detection ≈ 96%, endurance floor held
- Hybrid multi-metric detection default; operator LOOSEN adjudication + HTTP demo
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

Research prototype — not production-ready.
