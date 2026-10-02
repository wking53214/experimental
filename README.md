# Self-Hardening Governance Architecture

Experimental research prototype for **asymmetric adaptive authority** in adaptive systems.

## Core thesis

Systems can autonomously *increase* constraints when evidence of boundary pressure emerges, but no observation generated inside the adaptive loop can grant authority to *reduce* constraints.

## Status

- **364 tests passing** (Phases 1–9)
- Closed-loop adaptive / generative adversary contained
- Multi-signal + multivariate anomaly detection
- Concept-drift aware baselines
- Anti-spoof semantic classification
- Usability floor (20% of original limit) under sustained pressure

## Quick start

```bash
pip install pytest numpy
python -m pytest tests/ -q
```

## Key modules

| Path | Role |
|------|------|
| `src/governance/governor.py` | Main governance loop, authority model |
| `src/governance/anomaly_detector.py` | Multi-signal adaptive anomaly detection |
| `src/governance/workload.py` | Semantic classification (anti-spoof) |
| `src/governance/multivariate.py` | Mahalanobis / generative detection |
| `src/governance/phase9d_concept_drift.py` | Online baselines + drift |
| `src/governance/authority.py` | TIGHTEN auto / LOOSEN human-gated |

## Reports

See `PHASE_*.md` and `PHASE_9_HARDENING_PROGRESS.md` for experimental results.

## License

Research prototype — not production-ready.
