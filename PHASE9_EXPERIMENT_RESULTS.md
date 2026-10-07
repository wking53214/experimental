# Phase 9 End-to-End Experiment Results

Script: `experiments/phase9_end_to_end.py` (`python -m experiments.phase9_end_to_end --seeds 5`, about 40s). Raw numbers: `experiments/phase9_results.json`.

## Setup

- Synthetic system: 5 correlated Gaussian metrics (latency, error_rate, throughput, cpu, queue_depth), 10% coefficient of variation. Detectors train on 120 clean steps.
- An attack episode is 40 steps. A 9C chromosome shifts each target metric by `(magnitude - 1)` standard deviations while its window is active. An attack is "detected" if the detector alarms on a step where an attack is active.
- Damage = sum of `|magnitude - 1| * duration`, normalized to [0, 1]. This is my own definition; 9C does not specify one.
- 9C runs 20 attacks x 10 generations per detector per seed, averaged over 5 seeds.
- "Calibrated" means the Mahalanobis threshold is set to the 99.9th percentile of chi-square with 5 degrees of freedom (4.53) instead of the default 3.0.
- "Hybrid fixed" is an experiment-local subclass that feeds the generative layer the real latest observation. The repo code is unchanged.

## Findings

### 1. The shipped hybrid detector is broken
`HybridDetectorPipeline.detect_anomalies` calls `self.stream.recent_observation()`, which does not exist on `MetricStream`. A `hasattr` guard hides this and passes `{}`. Missing metrics default to 0, so the Mahalanobis distance is huge on every step.

| | step false-positive rate |
|---|---|
| hybrid as shipped | **100%** (flags every step) |

Its 100% detection rate in the table below is meaningless.

### 2. The default Mahalanobis threshold of 3.0 is wrong for multivariate data
3.0 is a univariate 3-sigma rule. With 5 metrics, about 11% of normal observations exceed it by chance, so the default generative detector alarms constantly.

### 3. Evolution finds evasive attacks against every working detector

| Detector | step FPR | detection, gen 0 | detection, last gen | evasive attacks found (of 200) | best undetected damage |
|---|---|---|---|---|---|
| traditional | 3.7% | 53% | 38% | 113 | 1.00 |
| generative, MD>3 | 15.7% | 96% | 99% | 3 | 0.18 |
| generative, calibrated | 0.4% | 84% | 49% | 91 | 1.00 |
| hybrid fixed, MD>3 | 10.7% | 94% | 97% | 11 | 0.48 |
| hybrid fixed, calibrated | 10.6% | 88% | 88% | 30 | 0.82 |
| hybrid as shipped | 100% | 100% | 100% | 0 | 0 |

- Detectors that are quiet on clean data are evadable. For the calibrated generative detector, detection falls from 84% to 49% over 10 generations, and the best evasive attack reaches the damage cap.
- Detectors that rarely miss are the ones that alarm on normal data. Strict thresholds buy detection with false positives, not with better discrimination.
- The calibrated generative detector had the best trade-off: 0.4% false positives, with 49% detection by the last generation. The traditional detector at 3.7% false positives did worse, at 38%.
- The hybrid did not beat its best component. Its false positives come from the `> 0.5` combined cutoff: the traditional layer's own rule (`> 0.85`) fires on 3.9% of clean steps, but at 0.5 it fires on 10.3%. The generative layer contributes 0.3%. Adding the generative layer helps detection a little (88% vs 38% last-gen) but only by inheriting the traditional layer's loose cutoff.

### 4. 9D misclassifies legitimate drift (200 steps, +20% on all metrics)
- The acceptance rate was 71.5%. 9D rejected 28.5% of legitimate observations as outliers, so the baseline lags behind real drift.
- It reported `has_drift = True`, `drift_type = "sudden"`. From the code: the drift reference distribution is frozen after the first 10 observations and never updated, and "sudden" is decided by magnitude (KL > 0.7), not by how fast the change happened. Any large, slow drift ends up classified as "sudden".
- False-positive rate under drift: traditional 19.5%, generative MD>3 28.5%, calibrated generative 1.5%, fixed hybrids 38.5%.

### 5. 9E works once one bug is accounted for (staged attack, 8 training and 8 test episodes)
- It learned the signature `"normal"` as a precursor, because normal-looking steps precede violations too. As shipped, every ordinary step then matches it: 64% of quiet steps raised a warning.
- With the `"normal"` signature removed: 7/8 violations warned about in advance, mean lead of 2.3 steps, and 1.9% false alarms on quiet steps.
- This is a small sample on one synthetic scenario.

## Caveats
- Synthetic Gaussian data suits the generative model's assumptions. Real metrics will be harder.
- One damage definition and one attack-effect model. Results shift with `SHIFT_SIGMA` and `DAMAGE_NORM`.
- 5 seeds, results averaged without confidence intervals.
- 9D and 9E are exercised standalone. The repo has no wiring between 9B, 9C, 9D and 9E, so this experiment composes them by hand.

## Suggested fixes (not applied)
1. Fix `HybridDetectorPipeline` to use `self.stream.get_latest()` and add a test with clean data that asserts a low false-positive rate.
2. Default the Mahalanobis threshold from chi-square quantiles for the metric count.
3. Use the traditional layer's own 0.85 cutoff (or tune the combined cutoff) in the hybrid.
4. In 9D, refresh the reference distribution and classify drift by rate, not just size.
5. In 9E, exclude the `"normal"` signature from learned patterns.
