# Baseline Poisoning (threat T2)

**Question.** An attacker who is present while the baseline is learned shapes what later counts as normal. How much does that weaken detection of the attack they launch afterwards, and can it be detected or mitigated?

Code: `experiments/baseline_poisoning.py`, `src/governance/baseline_check.py`, and the `trim` option of `src/governance/calibrated_detector.py`. Raw results: `experiments/baseline_poisoning_results.json`, `experiments/baseline_check_results.json`.

## Setup

Synthetic system of the Phase 9 experiments (5 correlated Gaussian metrics, about 10% coefficient of variation), baseline of 200 steps, 100 repetitions per cell (so each rate is good to about +/-10 points). Poison types, all aimed at `latency`, which is the metric the later attack shifts:

- **contaminate p**: a fraction p of baseline steps have latency shifted by +3 sd, the same direction and size as the later attack, at random positions. This is the attacker-knows-the-probe worst case.
- **ramp**: latency climbs linearly from 0 to +3 sd across the baseline ("boiling frog").
- **inflate k**: latency noise multiplied by k on every baseline step.

Probe: 30 clean steps, then latency +s sd for 20 steps. "Detected" means any alarm inside the 20-step window. Detectors are fitted on the poisoned baseline and then frozen, except the generative detector, which keeps learning.

## Result 1: poisoning is cheap and works

Detection of a **3 sd** shift (the same size as the poison), by baseline:

| Baseline | z-score | mahalanobis | generative | calibrated | calibrated + trim 10% | calibrated + trim 25% |
|---|---|---|---|---|---|---|
| none | 99% | 100% | 100% | 100% | 100% | 100% |
| contaminate 5% | 69% | 99% | 89% | 99% | 100% | 100% |
| contaminate 10% | 21% | 44% | 38% | 89% | 100% | 100% |
| contaminate 20% | 3% | 12% | 9% | 63% | 66% | 96% |
| contaminate 40% | 3% | 3% | 2% | 46% | 34% | 39% |
| ramp to +3sd | 3% | 5% | 3% | 63% | 15% | 19% |
| inflate noise x2 | 3% | 95% | 76% | 99% | 100% | 100% |
| inflate noise x3 | 3% | 75% | 45% | 96% | 94% | 94% |

Detection of a **2 sd** shift:

| Baseline | z-score | mahalanobis | generative | calibrated | calibrated + trim 10% | calibrated + trim 25% |
|---|---|---|---|---|---|---|
| none | 50% | 100% | 100% | 100% | 100% | 100% |
| contaminate 5% | 13% | 36% | 26% | 56% | 100% | 100% |
| contaminate 10% | 5% | 6% | 7% | 42% | 99% | 100% |
| contaminate 20% | 3% | 3% | 3% | 33% | 31% | 71% |
| contaminate 40% | 3% | 2% | 2% | 31% | 15% | 17% |
| ramp to +3sd | 3% | 2% | 2% | 10% | 1% | 3% |
| inflate noise x2 | 3% | 60% | 45% | 97% | 92% | 93% |
| inflate noise x3 | 3% | 48% | 33% | 89% | 84% | 87% |

- **A few percent contamination is enough.** 5% of the baseline (10 steps of 200) takes 2 sd detection from 100% to 13% to 36% for the z-score, Mahalanobis and generative detectors. 20% contamination takes 3 sd detection to 3% to 12%.
- **A slow ramp is as bad**: 3 sd detection falls to 3% to 5%.
- **Variance inflation** hurts the z-score detector badly (3 sd detection 3%) and the others much less.
- **The generative detector is no more robust than a frozen one**, despite updating online. The poison is already in its model by the time the attack starts.
- Strong attacks mostly survive: a 5 sd shift is still caught in most cells. The exceptions are 40% contamination (z-score 7%, Mahalanobis 18%, generative 16%) and the z-score detector under noise inflation (4% to 17%).

False-alarm rate on clean data, for reference (this is also what sets the chance floor in the next table):

| Baseline | z-score | mahalanobis | generative | calibrated | calibrated + trim 10% | calibrated + trim 25% |
|---|---|---|---|---|---|---|
| none | 0.1% | 0.1% | 0.1% | 2.3% | 1.4% | 1.8% |
| contaminate 5% | 0.1% | 0.1% | 0.1% | 0.5% | 1.0% | 1.4% |
| contaminate 10% | 0.1% | 0.1% | 0.1% | 0.8% | 0.8% | 0.9% |
| contaminate 20% | 0.1% | 0.1% | 0.1% | 1.4% | 0.8% | 0.8% |
| contaminate 40% | 0.1% | 0.1% | 0.1% | 2.0% | 1.1% | 1.3% |
| ramp to +3sd | 0.1% | 0.2% | 0.1% | 0.5% | 0.1% | 0.1% |
| inflate noise x2 | 0.1% | 0.3% | 0.1% | 3.1% | 2.1% | 2.6% |
| inflate noise x3 | 0.1% | 0.8% | 0.2% | 4.7% | 3.5% | 4.0% |

Share of clean control windows (same 20 steps, no attack) with any alarm. Detection numbers above include this chance floor, so the calibrated detector's higher rate in the clean row partly reflects its higher false-alarm rate (0.5% to 4.7%, against 0.1% to 0.8% for the Gaussian-calibrated ones), not better sensitivity:

| Baseline | z-score | mahalanobis | generative | calibrated | calibrated + trim 10% | calibrated + trim 25% |
|---|---|---|---|---|---|---|
| none | 1% | 5% | 5% | 31% | 24% | 33% |
| contaminate 5% | 1% | 2% | 2% | 9% | 18% | 23% |
| contaminate 10% | 1% | 2% | 2% | 13% | 19% | 19% |
| contaminate 20% | 1% | 1% | 2% | 20% | 17% | 16% |
| contaminate 40% | 1% | 2% | 2% | 25% | 19% | 20% |
| ramp to +3sd | 1% | 4% | 2% | 9% | 2% | 1% |
| inflate noise x2 | 1% | 5% | 5% | 38% | 36% | 36% |
| inflate noise x3 | 1% | 14% | 9% | 61% | 47% | 48% |

## Result 2: robust fitting helps against light contamination, and not otherwise

`CalibratedMahalanobisDetector(trim=0.25)` refits without the 25% of points furthest from the model and sets the threshold from the median score instead of the upper tail. In the table above, compare "calibrated" with "calibrated + trim":

- At **10% contamination** it helps everywhere I checked: +15 to +20 points of detection across three covariance structures (`experiments/trim_covariance_check.py`, 80 repetitions per cell).
- At **20%** it helps by 7 to 29 points depending on the covariance (the smallest gain is within noise).
- At **40%** there is no gain: the poisoned data is the majority, so the "robust" fit learns it.
- Against the **ramp** it does not help (3 sd detection 15% to 19%). The median that sets its threshold moves with the ramp.
- Against **inflation** it does not help.
- On clean data it holds a false-alarm rate of 1.4% to 1.8% against a 1% target. The threshold now assumes Gaussian metrics, so it gives up the empirical calibration that was this detector's purpose on real data (`BASELINE_COMPARISON.md`, section 4).

## Result 3: contamination of the baseline can often be detected from the baseline

`baseline_check.check_baseline(X)` compares three statistics with clean Gaussian data of the same size: the share of points beyond the chi-square 99% cutoff under a robust fit ("tail"), the distance between the ordinary and robust means ("shift"), and the distance between the first-half and second-half means ("half"). The total false-flag budget is 5%, split across the three.

| Baseline | Flagged (95% CI) | by tail | by shift | by half |
|---|---|---|---|---|
| none | 8% [5%, 13%] | 2% | 4% | 3% |
| contaminate 5% | 98% [95%, 99%] | 95% | 46% | 3% |
| contaminate 10% | 100% [98%, 100%] | 100% | 100% | 2% |
| contaminate 20% | 94% [89%, 96%] | 84% | 94% | 2% |
| contaminate 40% | 7% [4%, 11%] | 1% | 4% | 2% |
| ramp to +3sd | 100% [98%, 100%] | 0% | 1% | 100% |
| inflate noise x2 | 8% [5%, 13%] | 2% | 4% | 3% |
| inflate noise x3 | 8% [5%, 13%] | 2% | 4% | 3% |

- **Light and moderate contamination (5% to 20%) and a ramp are flagged 94% to 100% of the time.**
- **Heavy contamination (40%) and variance inflation are not detected** (7% to 8%, which is the same as clean data). When the poisoned data is the majority, or looks like a noisier but ordinary system, the data alone cannot tell.
- **The clean false-flag rate is 8% [5%, 13%]**, above the 5% budget (the simulated thresholds are noisy and the statistics are correlated). Expect around one clean baseline in twelve to be flagged.
- The contamination here is the same size as the later attack. Smaller poison would be harder to see, and also less harmful, but I did not measure that.
- It assumes clean data is roughly Gaussian and stationary. **Real telemetry is neither** (see `BASELINE_COMPARISON.md`), so on real data expect many false flags. I did not measure this on the Server Machine Dataset.

## What is wired in

- `CalibratedMahalanobisDetector.fit(X, require_clean=True)` refuses a flagged baseline.
- `Governor(baseline_check_at=N)` (generative detection, off by default) checks each boundary's first N observations once, writes the result to the audit log, and puts a suspicious one in `Governor.baseline_reviews` for a person. It never blocks anything automatically.

## Conclusions

1. Baseline poisoning is a real, cheap attack on every detector tested here, and the detector's online learning does not protect against it.
2. The effective defenses are procedural: do not learn the baseline while the system is exposed to an untrusted party, and have a person confirm a baseline window. The check and the robust fit reduce risk at moderate contamination, and are blind to heavy contamination and to variance inflation.
3. The generative detector, now the default, learns its baseline continuously from the first observations, so it has no clean window to check unless `baseline_check_at` is used. The honest summary: its baseline is as trustworthy as the first N observations were.

## Caveats

- Synthetic Gaussian data, one poisoned metric, one probe direction, 100 repetitions per cell.
- The poison always matches the later attack in direction and size. It is a worst case for the attacker's knowledge, not for poison size or placement.
- Contamination is placed at random positions. Contamination concentrated at the end of the window, or timed to the calibration split of the calibrated detector, was not tried.
