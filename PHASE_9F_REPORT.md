# Phase 9F: Temporal Shift Detection

**Status:** Complete, opt-in | **Tests:** 21 passing | **Source lines:** about 120 (`phase9f_temporal.py`)

## Why it exists
The Phase 9A/9B detectors judge one observation at a time, and 9A's model keeps learning from everything it sees. The end-to-end experiment showed that evolved attacks exploit both: a small shift held over many steps never crosses a per-step threshold, and the model absorbs it as normal. A sustained +1 sigma shift was caught in 5 of 20 episodes with the model updating and 12 of 20 with it frozen.

## How it works
`TemporalShiftDetector` learns a reference mean and covariance from the first 100 observations and freezes it. Each observation is whitened against that reference and folded into a multivariate exponentially weighted moving average. The statistic is compared with a threshold calibrated by simulation to a target false-alarm rate (0.5% per step by default), including the error from estimating the reference from only 100 observations. Evidence accumulates over time, so a small persistent shift eventually alarms.

- `smoothing` (default 0.2): lower values remember longer and catch smaller shifts more slowly.
- `reanchor()`: discard the reference and relearn it from the next 100 observations.
- Enable it on the hybrid with `HybridDetectorPipeline(temporal_shift=True)`. It is off by default.
- With it on, the hybrid also runs the 9D `ConceptDriftDetector`. Gradual drift re-anchors the layer from the latest 100 observations; an abrupt shift keeps alarming until `acknowledge_shift()` is called. `reanchor_on_drift=False` keeps a strict frozen reference.

## Second statistic: dispersion
The mean-tracking statistic alone only catches shifts. A second statistic (on by default, `dispersion=True`) tracks an EWMA of the whitened squared norm, two-sided. It catches changes that leave the means alone: wider spread, oscillation, and broken correlations between metrics. The false-alarm budget (0.5% per step) is split between the two statistics. Results by attack family and strength are in `PHASE9_EXPERIMENT_RESULTS.md`; in short, it roughly halves the weakest detectable shift, oscillation, or correlation break, and cannot reach attacks below the noise. A spread reduction on a single metric is barely detected.

## Results
See "Adversarial feedback loop" in `PHASE9_EXPERIMENT_RESULTS.md`. In short: a sustained +1 sigma shift is caught 20 of 20 times at 0.5% or fewer false positives. Against a freshly evolved adversary, detection at the last generation rose from 36-58% to 78-98% across three rounds, and the best undetected damage fell from 1.00 to 0.31-0.59.

## Tests
11 tests in `tests/test_phase9f_temporal.py`: never alarms while learning, low false-alarm rate on clean data, detects a small sustained shift, detects a large abrupt shift, the frozen reference does not absorb a long attack, `reanchor()` relearns, constant metrics do not crash, invalid smoothing is rejected, off by default on the hybrid, flags a sustained shift when enabled, six tests for the dispersion statistic (clean false alarms, variance inflation, oscillation, broken correlation, which statistic fired, can be disabled), following gradual drift cuts false alarms versus a frozen reference, an abrupt shift is held until acknowledged, `reanchor()` from recent observations has no blind period, and the drift wiring is inert when the layer is off. Code coverage was not measured.

## Limitations
- Trade-off between drift and slow attacks: with drift re-anchoring on, a slow attack lasting longer than the drift window is partly absorbed (about half of a 300-step +1 sigma attack); with it off, legitimate drift alarms constantly. Measured numbers are in `PHASE9_EXPERIMENT_RESULTS.md`. This is why the layer is opt-in.
- Even wired, the layer adds false alarms on legitimate drift (35% of plateau steps after a +20% ramp).
- Attacks weaker than about 0.5 to 1 sigma (shifts), 1.5x (spread), or 60% replacement (correlation breaks) over 20 steps are at chance level.
- Detection rates in the experiments count any alarm during the attack window, so they include a chance floor of about 35-40% for a 20-step window.
- The remaining evasive attacks are mostly very small shifts (under 1 sigma).
- Evidence is from synthetic Gaussian data.
