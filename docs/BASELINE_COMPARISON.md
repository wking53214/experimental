# Baseline Comparison: does the machinery beat a plain threshold?

Two plain baselines, fitted once on a clean baseline and then frozen, run through the same experiments as the project's detectors:

- **z-score**: alarm if any metric's |z| exceeds a Bonferroni-corrected cutoff.
- **Mahalanobis**: alarm if the distance exceeds a chi-square cutoff (ridge-regularised covariance).

Code: `experiments/baselines.py`, `experiments/baseline_comparison.py` (synthetic), `experiments/real_data_smd.py` (real). Raw results: `experiments/baseline_comparison_results.json`, `experiments/real_data_smd_results.json`.

## 1. Synthetic data, evolved attacker

Setup as in `PHASE9_EXPERIMENT_RESULTS.md` (5 correlated Gaussian metrics, 5 seeds, 200 evolved attacks per seed per detector). The attacker evolves against each detector separately. "Detected" is the share of evolved attacks that were caught; false-alarm rate is measured on clean episodes.

| Detector | Step false-alarm rate | Detected (all 200) | Detected (last generation) | Attacks that evaded |
|---|---|---|---|---|
| z-score, nominal 0.1% | 0.1% | 41% | 36% | 118 |
| z-score, nominal 1% | 0.8% | 71% | 76% | 58 |
| z-score, nominal 4% | 4.0% | 83% | 84% | 34 |
| Mahalanobis, nominal 0.1% | 0.4% | 62% | 62% | 76 |
| Mahalanobis, nominal 1% | 2.3% | 87% | 88% | 25 |
| Mahalanobis, nominal 4% | 7.0% | 97% | 99% | 5 |
| generative (default) | 0.4% | 54% | 48% | 93 |
| hybrid (default) | 3.8% | 62% | 60% | 75 |
| hybrid + temporal | 4.4% | 77% | 83% | 46 |

Reading it:

- **The hybrid is dominated.** Plain Mahalanobis at nominal 1% has a *lower* false-alarm rate (2.3%) than the hybrid (3.8%) and catches far more (87% vs 62%). At 4.0%, a plain z-score catches 83%, more than hybrid + temporal at 4.4% (77%).
- **At the same false-alarm budget the generative detector is no better than the plain Mahalanobis baseline** (54% vs 62% at about 0.4%). Its online updates did not buy detection.
- Detection rates rise with false-alarm rate partly by chance, because any alarm inside the attack window counts (see the chance floor in `PHASE9_EXPERIMENT_RESULTS.md`). That does not change the comparisons above, where the baseline has both the lower false-alarm rate and the higher detection.
- Calibration error: the frozen Mahalanobis baseline, fitted on 120 steps of 5 metrics, false-alarms about 2 to 4 times its nominal rate. Fitting a covariance on little data inflates distances on new data.

Where the baselines lose: legitimate drift. Under a +20% drift over 200 steps (one seed):

| Detector | False-alarm rate under drift |
|---|---|
| z-score baseline | 9.5% |
| Mahalanobis baseline | 26% |
| traditional | 20% |
| generative (default) | 1.5% |
| hybrid (default) | 20% |
| hybrid + temporal | 63% |

Only the generative detector, which keeps updating its model, copes with drift. The cost is the open trade-off already documented: whatever follows drift also follows a slow attack.

## 2. Real data: Server Machine Dataset

SMD (Su et al., KDD 2019): 38 server metrics per machine, labeled anomaly segments, a training segment followed by a test segment. Four machines (1-1, 2-1, 3-1, 1-6), 23.7k to 28.7k test steps each. Fetch with `scripts/fetch_smd.sh` (not committed). Metrics constant in the training segment were dropped, leaving 30 to 33.

Event recall is the share of labeled anomaly segments with at least one alarm inside. Mean over the four machines:

| Detector | Step false-alarm rate | Event recall | Point recall |
|---|---|---|---|
| traditional | 99.9% | 100% | 100% |
| hybrid (default) | 99.9% | 100% | 100% |
| hybrid + temporal | 100% | 100% | 100% |
| generative (default) | **10.6%** | 99.2% | 66.7% |
| z-score, default (nominal 0.1%) | 45.8% | 99.2% | 82.3% |
| Mahalanobis, default (nominal 0.1%) | 60.6% | 99.2% | 87.5% |
| z-score, threshold set to the generative detector's false-alarm rate | 9.4% | 98.3% | 52.0% |
| Mahalanobis, threshold set to the generative detector's false-alarm rate | 10.6% | 97.5% | 55.7% |

Reading it:

1. **The traditional layer, and therefore the default hybrid, alarms on essentially every step of real 30-metric telemetry.** The rule alarms whenever any 2 metrics are more than 2σ from baseline, with no allowance for how many metrics exist. With 30 independent Gaussian metrics that alone gives about a 39% false-alarm rate per step. Real metrics are heavy-tailed and bursty, which pushes it to about 99%. A baseline window of 100, 1000 or 5000 steps makes no difference. The rule was tuned on 5 metrics.
2. **Every Gaussian-calibrated threshold fails on real data.** A cutoff meant for 0.1% false alarms gives 12% to 84% on SMD (z-score and Mahalanobis, default). Real metrics are not Gaussian, and the test segment drifts from the training segment.
3. **The generative detector is the only project component that is usable, and it is about level with frozen baselines at the same false-alarm rate, not clearly better.** Pooled point recall is 67% vs 52% to 56%, but per machine it is mixed: ahead on machines 1-1 and 3-1, behind on 2-1 (26% vs 37%), a tie on 1-6. The baselines get an oracle threshold taken from the test normals, which favors them. They are frozen while the generative detector keeps updating, which favors it.
4. **A 10.6% false-alarm rate is still far too high to act on.** That is an alarm about every 9 steps.
5. Event recall is near its ceiling for everything, because the labeled segments are long. It does not separate the detectors; false-alarm rate and point recall do.

## 3. Attempt to repair the traditional layer (done, partly)

Two changes, both default-safe (existing behavior and all earlier tests unchanged):

- The multi-metric rule now requires `required_anomalous_metrics(n)` metrics beyond 2σ instead of a fixed 2: still 2 up to 7 metrics, 3 at 10, 4 at 20 to 30, 5 at 38 (the smallest count whose chance under independence is at most 5%).
- `baseline_observations` (default 15, unchanged) sets how many observations the baseline is learned from. The default 15 is far too few for autocorrelated telemetry: the learned spread was about 10x smaller than the real spread (median 0.003 vs 0.05), so nearly every metric looked abnormal.

Result on SMD: **not repaired.** With the rule scaled, the traditional layer still alarmed on 99.7% to 100% of steps. Giving it a baseline of the 2000 most recent training points brought false alarms to 4% to 30%, and where it was quiet it missed almost everything (event recall collapsing, point recall 4% to 8% on machine 2-1). The rule is a fixed "count of metrics beyond 2σ of a once-learned baseline", which assumes stationary, roughly Gaussian metrics. Tuning it does not change that. The default hybrid is therefore still unusable on real many-metric telemetry.

## 4. Calibrating from the data instead of a formula

`src/governance/calibrated_detector.py` (`CalibratedMahalanobisDetector`): Mahalanobis distance whose alarm threshold is the empirical (1 - target) quantile of its own scores on the most recent 30% of the baseline, scored the way it runs in service. Optionally the mean and covariance follow slow change from steps that score below a multiple of the threshold, so gross outliers are not learned.

Protocol, fixed before looking at held-out data (`experiments/calibrated_smd.py`): configurations chosen on 4 development machines; judged on 8 machines (1-2, 1-3, 1-4, 2-2, 2-3, 2-4, 3-2, 3-3) not used for any choice. Target false-alarm rate 1%.

Held-out machines (mean over 8):

| Detector | Median realized FPR | Mean realized FPR | Machines within 3x of target | Event recall | Point recall |
|---|---|---|---|---|---|
| calibrated, frozen | 3.0% | 8.8% | 5 of 8 | 75% | 25% |
| calibrated, adapting (selected on dev) | 3.0% | 7.3% | 5 of 8 | 77% | 27% |
| generative detector | 7.0% | 6.7% | 0 of 8 | 99% | 41% |
| Mahalanobis, chi-square cutoff | 22.9% | 31.0% | 0 of 8 | 99% | 59% |

Reading it:

- **Calibration from data is much closer to target** than any Gaussian formula (median 3% vs 7% to 23%), but still three times the 1% target at the median, and 3 of 8 machines remain far over (up to 41%). The development machines were worse (mean 24%).
- **It buys that by missing anomalies.** On machines 1-3 and 1-4 the frozen calibrated detector catches 17% of anomaly segments; the generative detector catches all of them, at about 7% false alarms.
- **No configuration achieves a calibrated rate reliably.** The dominant error is the shift between the training and test segments, which a threshold fitted on training data cannot see. Adaptation helps on some machines and hurts on others, and a change that moves in the wrong direction can be learned (excluding flagged points can also get stuck after a level shift).
- Taken together: nothing tried here beats the generative detector in an unambiguous way. Better calibration costs recall, and which way to trade is a decision about how many false alarms are acceptable, not something these data settle.

## 5. What is available

- `Governor(detection="generative")` runs only the generative layer (scored before it learns each point). **It is now the default** (changed 2026-10-07 at the project owner's decision); `detection="hybrid"` keeps the old behavior. Two existing tests that asserted the hybrid default were updated, and the hybrid path is still tested.
- Scoring note: the hybrid's generative layer ingests an observation before scoring it, so a point partly explains itself. The generative-only pipeline scores first.

## Caveats

- One real dataset family, four machines. The labels, and the assumption that the training segment is clean, come from the dataset authors.
- The evolved-attacker numbers come from synthetic Gaussian data with a 40-step episode and a damage measure I defined.
- Point recall depends on where the threshold sits; the matched rows set it from test normals, which a real deployment cannot do.
- No claim here about the temporal layer on real data beyond that it inherits the traditional layer's failure through the hybrid.

## What this means for the project

- Do not describe the hybrid as an improvement over simple thresholds. On this evidence it is not.
- The default `Governor` uses the hybrid. On real telemetry with many metrics that means a recorded violation on almost every step, which feeds the tightening loop (see T3 in `THREAT_MODEL.md`). Scaling the rule and enlarging the baseline (section 3) did not fix this; `Governor(detection="generative")` avoids it.
- The detection result to build on is the generative layer, compared against plain Mahalanobis, not the hybrid. Even that false-alarms about 7% of steps on unseen real machines.
