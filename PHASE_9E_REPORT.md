# Phase 9E: Attack Precursor Learning (Early Warning)

**Status:** Implemented and tested | **Tests:** 17 in `tests/test_phase9d_e.py`, all passing | **Source:** `src/governance/phase9e_precursors.py` (255 lines)

## What it is
A learner that looks at what metric patterns appeared shortly before past violations, and raises a warning when one appears again.

## How it works
- **`AttackPrecursorLearner`**: keeps a bounded history of observations (`lookback_window * 10`) and the positions of violations (as absolute sequence numbers; evicted violations are dropped). `learn_precursors()` looks 1, 2, 3 and 5 steps before each recent violation, and describes each observation by a signature: which metrics are more than 1.5 standard deviations from the history's mean (for example `elevated:4`). Signatures seen at least `min_patterns` times (default 3) are kept, with an average lead time and a confidence (occurrences divided by violations). The signatures `"normal"` and `"unknown"` are never learned.
- **`EarlyWarningSystem`**: for each observation, checks for a learned signature. A match gives "elevated" with confidence 0.5 times the pattern's confidence; a match plus an anomaly score above 0.5 gives "critical" with confidence 0.9. Learning happens only when `update_from_violation()` is called.

## What the experiments show
On a synthetic staged attack (queue depth +30% for 3 steps, then an error-rate spike counted as a violation; 8 training and 8 test episodes): a warning came before 7 of 8 violations, about 2.3 steps early, with 1.9% false alarms on quiet steps. This is a small sample on one scenario.

Defects found and fixed: the learner treated the `"normal"` signature as a precursor (64% false alarms on quiet steps), and violation positions went stale once the history wrapped.

## Measured cost
About 0.10 ms per observation with a learned pattern present (5 metrics); nearly free with none.

## Integration
Standalone: nothing in `src` imports it. The experiments call it directly and supply the violation labels.

## Limitations
- Signatures are metric positions in dictionary order, so metric order must stay stable.
- The `predictive_accuracy` in the summary is a rough placeholder (violations divided by detections), not a measured accuracy.
- Only a violation label from outside can teach it; it cannot learn without violations, and it learns only when asked.
- Evidence is from one synthetic scenario with 8 test episodes.
