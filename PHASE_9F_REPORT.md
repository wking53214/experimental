# Phase 9F: Temporal Shift Detection

**Status:** Complete, opt-in | **Tests:** 11 passing | **Source lines:** about 120 (`phase9f_temporal.py`)

## Why it exists
The Phase 9A/9B detectors judge one observation at a time, and 9A's model keeps learning from everything it sees. The end-to-end experiment showed that evolved attacks exploit both: a small shift held over many steps never crosses a per-step threshold, and the model absorbs it as normal. A sustained +1 sigma shift was caught in 5 of 20 episodes with the model updating and 12 of 20 with it frozen.

## How it works
`TemporalShiftDetector` learns a reference mean and covariance from the first 100 observations and freezes it. Each observation is whitened against that reference and folded into a multivariate exponentially weighted moving average. The statistic is compared with a threshold calibrated by simulation to a target false-alarm rate (0.5% per step by default), including the error from estimating the reference from only 100 observations. Evidence accumulates over time, so a small persistent shift eventually alarms.

- `smoothing` (default 0.2): lower values remember longer and catch smaller shifts more slowly.
- `reanchor()`: discard the reference and relearn it from the next 100 observations.
- Enable it on the hybrid with `HybridDetectorPipeline(temporal_shift=True)`. It is off by default.

## Results
See "Adversarial feedback loop" in `PHASE9_EXPERIMENT_RESULTS.md`. In short: a sustained +1 sigma shift is caught 20 of 20 times at 0.5% or fewer false positives. Against a freshly evolved adversary, detection at the last generation rose from 36-58% to 78-98% across three rounds, and the best undetected damage fell from 1.00 to 0.31-0.59.

## Tests
11 tests in `tests/test_phase9f_temporal.py`: never alarms while learning, low false-alarm rate on clean data, detects a small sustained shift, detects a large abrupt shift, the frozen reference does not absorb a long attack, `reanchor()` relearns, constant metrics do not crash, invalid smoothing is rejected, off by default on the hybrid, and flags a sustained shift when enabled. Code coverage was not measured.

## Limitations
- The reference is frozen, so legitimate long-term drift eventually alarms: on a +20% drift scenario the hybrid with this layer alarms on 72% of steps (19.5% without). `reanchor()` is not yet wired to the 9D drift detector, which is why the layer is opt-in.
- It targets persistent shifts in the mean. Variance changes, correlation changes that preserve the means, and oscillating attacks are not addressed and were not tested.
- The remaining evasive attacks are mostly very small shifts (under 1 sigma).
- Evidence is from synthetic Gaussian data.
