# Self-Hardening Governance Architecture

[![CI](https://github.com/wking53214/experimental/actions/workflows/ci.yml/badge.svg)](https://github.com/wking53214/experimental/actions/workflows/ci.yml)

Experimental research prototype for **asymmetric adaptive authority** in adaptive systems.

## Core thesis

Systems can autonomously *increase* constraints when evidence of boundary pressure emerges, but **no observation generated inside the adaptive loop may grant authority to reduce or disable constraints**.

## Status

- **375 tests passing** (Phases 1–12)
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

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/PAPER_DRAFT.md](docs/PAPER_DRAFT.md) | Draft manuscript |
| [docs/PAPER_OUTLINE.md](docs/PAPER_OUTLINE.md) | Section outline |
| [docs/FIGURES.md](docs/FIGURES.md) | Mermaid diagrams |
| [docs/openapi-adjudication.yaml](docs/openapi-adjudication.yaml) | OpenAPI |
| [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) | Reproducibility |
| [PHASE_12_COMPLETION_REPORT.md](PHASE_12_COMPLETION_REPORT.md) | Latest phase report |

## Phase index

| Phase | Focus |
|-------|--------|
| 1–9 | Core loop through full suite green |
| 10 | Multi-seed, hybrid default, adjudication, reproducibility |
| 11 | Outline, CI, stochastic seeds, HTTP demo |
| 12 | Draft prose, OpenAPI, figures, README polish |

## License

Research prototype — not production-ready.
