# Phase 9 End-to-End Experiment Results

Script: `experiments/phase9_end_to_end.py` (`python -m experiments.phase9_end_to_end --seeds 5`, about 40s). Raw numbers: `experiments/phase9_results.json`.

## Setup

- Synthetic system: 5 correlated Gaussian metrics (latency, error_rate, throughput, cpu, queue_depth), 10% coefficient of variation. Detectors train on 120 clean steps.
- An attack episode is 40 steps. A 9C chromosome shifts each target metric by `(magnitude - 1)` standard deviations while its window is active. An attack is "detected" if the detector alarms on a step where an attack is active.
- Damage = sum of `|magnitude - 1| * duration`, normalized to [0, 1]. This is my own definition; 9C does not specify one.
- 9C runs 20 attacks x 10 generations per detector per seed, averaged over 5 seeds.
- "Default" (calibrated) means the Mahalanobis threshold is the 99.9th percentile of chi-square with 5 degrees of freedom (4.53). "MD>3" passes 3.0 explicitly.
- The experiment runs the repo's real `HybridDetectorPipeline` and `GenerativeAnomalyDetector`.

## Findings

### 1. The shipped hybrid detector was broken (now fixed)
`HybridDetectorPipeline.detect_anomalies` called `self.stream.recent_observation()`, which does not exist on `MetricStream`. A `hasattr` guard hid this and passed `{}`. Missing metrics default to 0, so the Mahalanobis distance was huge on every step.

| | step false-positive rate |
|---|---|
| hybrid as shipped (before the fix) | **100%** (flagged every step) |
| hybrid after the fix | 10.6% |

The fix uses `self.stream.get_latest()`. The remaining 10.6% came from the loose combined cutoff, fixed below (finding 3); the hybrid is now at 3.8%.

### 2. The default Mahalanobis threshold of 3.0 was wrong for multivariate data (now fixed)
3.0 is a univariate 3-sigma rule. With 5 metrics, about 11% of normal observations exceed it by chance. `GenerativeAnomalyDetector` now calibrates its default threshold from the number of metrics (the square root of the chi-square 99.9th percentile, approximated without scipy: 4.53 for 5 metrics) and sets saturation 2.0 above it. An explicit threshold is still respected.

### 3. Evolution finds evasive attacks against every working detector
Re-run after the wiring, threshold, and cutoff fixes, using the repo's real classes (5 seeds, 200 evolved attacks per seed per detector):

| Detector | step FPR | detection, gen 0 | detection, last gen | evasive attacks found (of 200) | best undetected damage |
|---|---|---|---|---|---|
| traditional | 3.7% | 53% | 38% | 113 | 1.00 |
| generative, MD>3 (explicit) | 15.7% | 96% | 99% | 3 | 0.18 |
| generative, default (calibrated) | 0.4% | 84% | 48% | 93 | 1.00 |
| hybrid, MD>3 (explicit) | 14.7% | 96% | 98% | 3 | 0.18 |
| hybrid, default | 3.8% | 76% | 60% | 75 | 0.93 |

For comparison, before the fixes the hybrid had a 100% false-positive rate as shipped, and 10.6% once the wiring was patched, because of its shared `> 0.5` cutoff.

- Detectors that are quiet on clean data are evadable. For the default generative detector, detection falls from 84% to 48% over 10 generations, and the best evasive attack reaches the damage cap.
- Detectors that rarely miss are the ones that alarm on normal data (the MD>3 rows, 15% false positives).
- The fixed hybrid detects more than either layer alone (60% in the last generation vs 38% traditional and 48% generative) at the traditional layer's false-positive rate (3.8%). It is a trade-off, not a clear win: the generative layer alone has 0.4% false positives.
- The hybrid's cutoff was the cause of its excess false positives. The traditional layer's own rule (`> 0.85`) fires on 3.9% of clean steps; at the old shared `> 0.5` cutoff it fired on 10.3%. Each layer now keeps its own decision rule, and the hybrid alarms if either fires.

### 4. 9D misclassified legitimate drift (now fixed)
Scenario: 200 steps, +20% on all metrics.

| | before | after |
|---|---|---|
| observations accepted into the baseline | 71.5% | 98.5% |
| drift label | "sudden" | none (reference re-anchored) |

Causes found along the way: the reference distribution was frozen after the first 10 observations; "sudden" was decided by size, not speed; the rejection cutoff was a fixed 3.0 (wrong for several metrics); and the KL value was summed over metrics, so thresholds meant different things for different metric counts. Fixes: re-anchor the reference to the current window when drift persists, detect sudden shifts by comparing the older and newer halves of a full window (held until `acknowledge()`), calibrate the rejection cutoff, and use the largest per-metric KL. One regression caught on the way: comparing half-windows before the window was full false-flagged clean data, so the check now waits for a full window. Over 20 seeds on 5-metric data: 0/20 false sudden flags on clean data, and abrupt shifts (one metric +3σ, one metric +5σ, all metrics +2σ) flagged 20/20.

False-positive rate under drift for the detectors themselves: traditional 19.5%, generative MD>3 28.5%, default generative 1.5%, hybrid MD>3 37%, default hybrid 19.5%.

### 5. 9E had a precursor-learning bug (now fixed)
It learned the signature `"normal"` as a precursor, because normal-looking steps precede violations too. Every ordinary step then matched it: 64% of quiet steps raised a warning.

| staged attack (8 train, 8 test episodes) | before | after |
|---|---|---|
| violations warned about in advance | 8/8 | 7/8 |
| mean lead time | 4.75 steps | 2.3 steps |
| false alarms on quiet steps | 64% | 1.9% |

The earlier 8/8 was inflated by the false alarms. This is a small sample on one synthetic scenario.

## Caveats
- Synthetic Gaussian data suits the generative model's assumptions. Real metrics will be harder.
- One damage definition and one attack-effect model. Results shift with `SHIFT_SIGMA` and `DAMAGE_NORM`.
- 5 seeds, results averaged without confidence intervals.
- 9D and 9E are exercised standalone. The repo has no wiring between 9B, 9C, 9D and 9E, so this experiment composes them by hand.

## Fixes
1. ~~Fix `HybridDetectorPipeline` wiring~~ (done, with regression tests).
2. ~~Default the Mahalanobis threshold from chi-square quantiles~~ (done).
3. ~~Use each layer's own decision rule in the hybrid~~ (done).
4. ~~In 9D, refresh the reference distribution and classify drift by rate~~ (done).
5. ~~In 9E, exclude the `"normal"` signature from learned patterns~~ (done).

All five are fixed. The evasion result in finding 3 is unchanged: evolved attacks still reach a high damage level undetected against every detector with an acceptable false-positive rate.
