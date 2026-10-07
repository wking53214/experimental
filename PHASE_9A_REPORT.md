# Phase 9A: Generative Anomaly Detection (Multivariate Gaussian)

**Status:** Implemented and tested | **Tests:** 35 passing (`tests/test_phase9a_multivariate.py`: 28 for the model, scorer, invariants and detector, plus 7 for the calibrated threshold) | **Source:** `src/governance/multivariate.py` (264 lines)

## What it is
A detector that learns what normal looks like across several metrics at once, including how they move together, and flags observations that are unlikely under that picture. The idea: an attacker can keep each metric looking fine while distorting the relationships between them.

## Components
- **`MultivariateGaussian`**: online mean and covariance (Welford-style update), regularized with `1e-6 * I`, with a pseudo-inverse fallback for singular matrices. Distance is the Mahalanobis distance. Missing metrics default to 0.
- **`calibrated_mahalanobis_threshold(n_metrics)`**: the distance exceeded by about 0.1% of normal observations in n dimensions (square root of the chi-square 99.9th percentile, approximated without scipy: 3.29 for 1 metric, 4.53 for 5, 6.73 for 20).
- **`MahalanobisAnomalyScorer`**: score 0 below the threshold, 1 at saturation, linear in between.
- **`InvariantLearner`**: stores the correlation of every metric pair. Its violation check is a heuristic (see limitations).
- **`GenerativeAnomalyDetector`**: learns for `min_observations` (default 20) before detecting. The threshold defaults to the calibrated value for the number of metrics (an explicit value is respected). `anomaly_detected` means the Mahalanobis score is above 0. The model keeps learning from every observation it ingests.

## What the experiments show
Detail and numbers are in `PHASE9_EXPERIMENT_RESULTS.md`.
- The old fixed threshold of 3.0 false-alarmed on about 11% of clean 5-metric observations. The calibrated default gives 0.4% per step.
- Against evolved attacks (shifts), the default detector caught 84% of the first generation and 48% of the last: evolution finds attacks it misses.
- It absorbs attacks while they run, because it keeps learning: a sustained +1 sigma shift on one metric was caught in 5 of 20 episodes with the model updating and 12 of 20 with it frozen. This is why Phase 9F exists.
- Correlation breaks (a metric keeps its mean and spread but loses its link to the others) are the weakest area: weak versions are at chance level.

## Measured cost
About 0.06 ms per observation (detect plus ingest, 5 metrics, one machine, pure Python and NumPy). The covariance and its inverse are recomputed on every call, because the cache-validity flag is never set.

## Integration
Used by `HybridDetectorPipeline` (Phase 9B) and, alone, by `GenerativePipeline`, which the `Governor` now creates per boundary by default (the hybrid is opt-in). Nothing else imports it.

## Limitations
- Assumes roughly Gaussian data; all experiments used synthetic Gaussian data.
- Learns from everything it sees, so a slow attack is absorbed into the model.
- The invariant check compares each metric to the mean across all metrics in the observation, which is meaningless when metrics have different scales, and it only looks at positive correlations above 0.5. Its result is reported but not used in the anomaly decision.
- No temporal modeling: each observation is judged on its own.
- A single normal regime; no support for multimodal behavior.
