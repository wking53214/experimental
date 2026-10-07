# Phase 9B: Hybrid Detection Pipeline

**Status:** Implemented and tested | **Tests:** 8 in `tests/test_phase9b_e_integration.py` plus 6 hybrid-integration tests in `tests/test_phase9f_temporal.py`, all passing | **Source:** `src/governance/phase9_integration.py` (133 lines)

## What it is
`HybridDetectorPipeline` extends the Phase 8C `DetectorPipeline` (rule-based, baseline-driven) with the Phase 9A generative detector, and optionally a temporal layer (Phase 9F). Every observation goes to every layer.

## Decision rule
Each layer keeps its own decision, and the hybrid alarms if any layer fires. The reported `anomaly_score` is the maximum of the layers' scores. The result also includes each layer's own output (`traditional_detection`, `generative_detection`, `temporal_detection`, `drift_status`).

## Options
- `mahalanobis_threshold`: passed to the generative layer (default: calibrated from the metric count).
- `temporal_shift` (default `False`): adds the Phase 9F temporal layer and a Phase 9D `ConceptDriftDetector`. Gradual drift re-anchors the temporal layer; an abrupt shift is held until `acknowledge_shift()` is called.
- `reanchor_on_drift` (default `True`): set `False` for a strict frozen reference.

## Defects found and fixed during the end-to-end experiment
1. The generative layer was handed an empty observation (the pipeline asked the stream for a method that does not exist, behind a `hasattr` guard), so it flagged every step: a 100% false-positive rate.
2. The default Mahalanobis threshold of 3.0 was a one-dimensional rule (about 11% false alarms on clean 5-metric data).
3. A shared `> 0.5` score cutoff made the traditional layer fire about three times as often as its own rule intends.

## Integration
`Governor.ingest_metrics` (`src/governance/governor.py`) creates one `HybridDetectorPipeline` per boundary, ingests each observation, calls `detect_anomalies()`, and records an execution plus violation event whenever `anomaly_detected` is true. Before fix 1, this would have recorded a violation on every observation after the generative layer's 20-observation warmup. The temporal option is not enabled by the governor.

## Measured
- Cost: about 0.08 ms per observation (ingest plus detect, 5 metrics); about 0.24 ms with the temporal layer and drift detector on.
- Clean-data false-positive rate per step: about 3.8% on the main experiment seeds (1.3% to 4.0% across other seed sets), almost all from the traditional layer: its own rule fires on 3.9% of clean steps and the generative layer on 0.3%.
- Detection against evolved attacks, and the trade-offs of the temporal option, are in `PHASE9_EXPERIMENT_RESULTS.md`.

## Limitations
- **Does not beat plain baselines.** At matched false-alarm rates a frozen Mahalanobis or z-score baseline detects more, and on real 30-metric telemetry the traditional layer alarms on about 99.9% of steps. See `docs/BASELINE_COMPARISON.md`.
- The traditional layer dominates the false-positive rate.
- The temporal option trades missed slow attacks against false alarms on legitimate drift, which is why it is off by default.
- Evolved attacks still evade it at weak strengths (see the results document).
