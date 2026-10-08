# Success Criteria (pre-registered)

Written and committed on 2026-10-08, **before** the value comparison (criterion 5) and the detector work (criteria 3 and 4) were run, so their outcomes cannot move the bar. Each criterion is a pass/fail threshold on a number a script produces. `experiments/scorecard.py` evaluates them from the result files and prints PASS, FAIL or NOT MEASURED. A criterion is only changed by a commit that says why; the old text stays in history.

What "works" means here, in plain terms: the system must (a) never let automation loosen a limit, (b) notice real trouble on real telemetry without crying wolf, (c) respond to real trouble without a human for routine cases, and (d) be better than the simple alternatives for that, not just safe.

| # | Claim | Measurement | Pass threshold |
|---|---|---|---|
| 1 | **Safety**: automation never loosens; every change is attributable | Test suite, fuzzing, and the integrity check at the end of every soak run (12 machines x 3 configurations) | All tests pass, and `integrity_ok` is true in every soak run |
| 2 | **Detection is not worse than the simple baseline** | On the 8 held-out SMD machines, the default detector's point recall versus a plain Mahalanobis baseline whose threshold is set (oracle, from the test normals) to the same false-alarm rate | Detector recall >= baseline recall on at least 6 of 8 machines |
| 3 | **No crying wolf in the closed loop** | Soak, default configuration, 12 real machines x 8,000 steps: tightenings with no labeled anomaly in the previous 100 steps | At most 1 unjustified tightening on at least 10 of 12 machines |
| 4 | **It responds to real trouble** | Same soak: a tightening occurs within 100 steps after a labeled anomaly begins, before the breaker is spent | True on at least 8 of 12 machines (machines with at least 3 labeled anomaly events in the window) |
| 5 | **Value over the alternatives** | `experiments/value_vs_alternatives.py`: damage admitted to a compromised key under the adaptive system versus a static limit and versus a human who acts after a delay, and legitimate traffic blocked in attack-free runs | Against a sustained attack that raises usage: at least 40% less admitted attack usage than the static limit, **and** legitimate traffic blocked at most 1% of steps in attack-free runs, **and** no unsafe loosening |
| 6 | **Baseline poisoning is survivable in the recommended configuration** | `baseline_poisoning` experiment, calibrated detector with the trimmed fit, 10% contaminated baseline | Detection of the 3 sd probe at least 80% |

Not in scope of any criterion, and so not claimed: protection against code running inside the process, network attackers, malicious operators, or attackers who stay below every limit and every detector.

Criteria deliberately left out because they cannot be measured with the data available: usefulness against real adversaries (SMD anomalies are faults, not attacks), and any claim about a specific production stack.

## Known status when this was written

Criterion 1 holds. Criteria 2, 3 and 4 are expected to fail with the current detector (Phase 13 found 3 unjustified tightenings on every machine). Criterion 5 had never been measured. Criterion 6 holds for the trimmed fit by the earlier experiment.
