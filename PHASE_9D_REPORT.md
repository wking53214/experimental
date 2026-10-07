# Phase 9D: Concept Drift and Adaptive Baseline

**Status:** Implemented and tested | **Tests:** 20 in `tests/test_phase9d_e.py`, all passing | **Source:** `src/governance/phase9d_concept_drift.py` (273 lines)

## What it is
Tools for telling legitimate change in a system apart from an attack, so a baseline can follow real drift without learning from anomalies.

## Components
- **`ConceptDriftDetector`**: compares the recent window (100 observations) with a reference distribution using the largest per-metric Gaussian KL divergence. Above 0.3 counts as drift. If drift persists with a full window and no sudden shift has been flagged, the reference is re-anchored to the current window. A shift is flagged "sudden" when the older and newer halves of a full window differ by more than 0.7 (a measure of speed, not size). A sudden shift is held, with no re-anchoring, until `acknowledge()` is called.
- **`OnlineAdaptiveBaseline`**: an exponential moving average (decay 0.95) of mean and variance. It rejects any observation whose Mahalanobis distance exceeds a cutoff, so anomalies are not learned. The cutoff defaults to a value calibrated from the metric count (3.29 for 1 metric, 4.53 for 5).
- **`AdaptiveDetector`**: runs both on each observation and keeps a history.

## What the experiments show
On a legitimate +20% drift over 200 steps (5 metrics): 98.5% of observations accepted into the baseline (was 71.5% before the fixes below), no false "sudden" label. On clean 5-metric data, 0 of 20 runs flagged sudden; abrupt shifts (one metric +3 sigma, one metric +5 sigma, all metrics +2 sigma) were flagged in 20 of 20 runs.

Defects found and fixed along the way: the reference was frozen after 10 observations; "sudden" was decided by size; the rejection cutoff was a fixed 3.0 (wrong for several metrics); the KL value summed across metrics, so thresholds depended on the metric count; and comparing half-windows before the window was full false-flagged clean data.

## Measured cost
About 0.15 ms per `AdaptiveDetector.process_observation` (5 metrics).

## Integration
- `ConceptDriftDetector` runs inside `HybridDetectorPipeline` when `temporal_shift=True` (off by default), where it re-anchors the Phase 9F temporal layer.
- `OnlineAdaptiveBaseline` and `AdaptiveDetector` are not used by any detector: nothing reads the baseline they build.

## Limitations
- Gaussian assumption, single regime.
- Drift versus slow attack is not separable from the data alone: a slow attack longer than the window is treated as drift and absorbed (see `PHASE_9F_REPORT.md`).
- Detection of drift needs a full window (100 observations).
