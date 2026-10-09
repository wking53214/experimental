# Success Criteria (pre-registered)

Written and committed on 2026-10-08, **before** the value comparison (criterion 5) and the detector work (criteria 3 and 4) were run, so their outcomes cannot move the bar. Each criterion is a pass/fail threshold on a number a script produces. `experiments/scorecard.py` evaluates them from the result files and prints PASS, FAIL or NOT MEASURED. A criterion is only changed by a commit that says why; the old text stays in history.

What "works" means here, in plain terms: the system must (a) never let automation loosen a limit, (b) notice real trouble on real telemetry without crying wolf, (c) respond to real trouble without a human for routine cases, and (d) be better than the simple alternatives for that, not just safe.

| # | Claim | Measurement | Pass threshold |
|---|---|---|---|
| 1 | **Safety**: automation never loosens; every change is attributable | Test suite, fuzzing, and the integrity check at the end of every soak run (12 machines x 3 configurations) | All tests pass, and `integrity_ok` is true in every soak run |
| 2 | **Detection is not worse than the simple baseline** | On the 8 held-out SMD machines, the default detector's point recall versus a plain Mahalanobis baseline whose threshold is set (oracle, from the test normals) to the same false-alarm rate | Detector recall >= baseline recall on at least 6 of 8 machines |
| 3 | **No crying wolf in the closed loop** | Soak, default configuration, 12 real machines x 8,000 steps: tightenings with no labeled anomaly in the previous 100 steps | At most 1 unjustified tightening on at least 10 of 12 machines |
| 4 | **It responds to real trouble** | Same soak: a tightening occurs within 100 steps after a labeled anomaly begins, before the breaker is spent | True on at least 8 of 12 machines (machines with at least 3 labeled anomaly events in the window) |
| 5 | **Value over the alternatives** | `experiments/value_vs_alternatives.py`: damage admitted to a compromised key under the adaptive system versus a static limit and versus a human who acts after a delay, and legitimate traffic blocked in attack-free runs | **Primary scenario, fixed before running:** a key whose usage is normally 50 +/- 5 (limit 100) is compromised at step 300 and its usage rises by +40 for 600 steps; damage is the sum of admitted usage above 50; 30 seeds, default `Governor`. Pass: at least 40% less damage than the static limit, **and** legitimate traffic blocked at most 1% of steps in attack-free runs, **and** no unsafe loosening. Other attack shapes are reported but do not decide the criterion. |
| 6 | **Baseline poisoning is survivable in the recommended configuration** | `baseline_poisoning` experiment, calibrated detector with the trimmed fit, 10% contaminated baseline | Detection of the 3 sd probe at least 80% |
| 7 | **A restart never loosens a limit** (added 2026-10-08, after the first scoring found the gap) | `tests/test_durable_state.py` (restart keeps limits and breaker state, refuses a tampered or forged log, fails closed if the log write fails) | All tests pass |
| 8 | **Outside signals can never loosen a limit** (added 2026-10-09) | `tests/test_signal_interface.py` (including a randomized hostile-input test) and the signal-endpoint tests in `tests/test_adjudication_server_hardening.py` | All tests pass |

Not in scope of any criterion, and so not claimed: protection against code running inside the process, network attackers, malicious operators, or attackers who stay below every limit and every detector.

Criteria deliberately left out because they cannot be measured with the data available: usefulness against real adversaries (SMD anomalies are faults, not attacks), and any claim about a specific production stack.

## Known status when this was written

Criterion 1 holds. Criteria 2, 3 and 4 are expected to fail with the current detector (Phase 13 found 3 unjustified tightenings on every machine). Criterion 5 had never been measured. Criterion 6 holds for the trimmed fit by the earlier experiment.

## Results (2026-10-08, first scoring)

`PYTHONPATH=. python -m experiments.scorecard`

| # | Result | What the pass or fail actually means |
|---|---|---|
| 1 Safety | PASS (36 of 36 soak runs, tests green) | Not falsified within the stated threat model. Found T13 afterwards (a restart resets limits), which this criterion did not look for. |
| 2 Detection vs baseline | PASS, bare minimum (6 of 8 machines) | The detector is at or above a plain Mahalanobis baseline on 6 of 8 and ahead clearly on none (recall within 0.05 on most). It matches the simplest alternative; it does not beat it. |
| 3 No crying wolf | **FAIL** (0 of 12 machines) | Every machine had 3 unjustified tightenings. |
| 4 Responds to real trouble | **FAIL** (0 of 6 eligible machines) | The breaker is spent on noise before real anomalies arrive. |
| 5 Value over alternatives | PASS, narrowly (43% against 40%) | The 43% is the breaker cap's arithmetic ((40 - 22.9) / 40), not detection quality. A human with perfect discrimination did better on sustained attacks (damage 6,960 to 9,664 against 13,783 for the system); the system wins only in the window before a slow human responds (a 100-step attack: 3,352 against 5,006). The clean-run cost was 0% because the key has 2x headroom; at 1.5x it was 0.41%. |
| 6 Poisoning | PASS (100%) | Only for the opt-in trimmed calibrated detector (Gaussian assumption). The default detector detects the 3 sd probe 38% of the time at 10% contamination. |

Criterion 3 was also tried with an alarm-persistence filter (`experiments/persistence_filter.py`): it did not help. Raw false alarms are sustained stretches caused by non-stationary data, not blips: a 3-of-5 filter cut held-out false alarms from 6.7% to 4.5% but dropped event recall from 0.99 to 0.66, and the strictest filter still left about 8 false episodes per machine at 0.20 recall.

**Criterion 7** was added after the first scoring, when Phase 13 and the positioning work showed that limits were not persisted. It is a test-based criterion, scored by running that file.

**Criterion 8** was added when the signal interface was built, in the same way as criterion 7: a test-based criterion scored by running those files.
