# Phase 13: Integrated System Test

Every earlier phase tested one control in isolation. This phase runs the controls together, under the current defaults (generative detection, breaker of 3, semantic layer on) plus an operator registry and an audit log, against real telemetry and against attackers.

Code: `tests/test_phase13_integration.py` (14 tests, synthetic, run in CI), `experiments/phase13_soak.py` (real data, not in CI), results `experiments/phase13_soak_results.json`.

## What was run

1. **Soak on real telemetry.** For each of 12 Server Machine Dataset machines: a `Governor` ingests the last 3,000 training steps, then the first 8,000 test steps, one at a time. After every step it runs detect, propose, authorize, apply. Three configurations: the defaults; `evidence_window=100` with `require_fresh_evidence`; and the breaker off.
2. **Integrated attack-and-tamper scenarios** (CI): flood, impersonation, acknowledgement, tampering with decisions, version history and the audit log, spoofed expected-load patterns.

## Findings

### 1. On real telemetry, false alarms alone exhaust the system's autonomy (new, important)

| Configuration (12 machines, 8,000 test steps each) | Tightenings | Final limit (start 100) | Unjustified tightenings |
|---|---|---|---|
| defaults (breaker 3) | 3 on every machine | 72.9 on every machine | 36 of 36 |
| evidence window 100 + fresh evidence | 3 on every machine | 72.9 on every machine | 34 of 36 |
| breaker off | 15 on every machine | 20.6 (the floor) on every machine | 180 of 180 |

"Unjustified" means no labeled anomaly in the previous 100 steps. The generative detector's false-alarm rate on these machines was 2.3% to 12% (median 6.0%). At that rate, any pattern rule fires within the first few hundred steps, so **the circuit breaker is doing all the work**: without it every machine is driven to the 20% usability floor by false alarms, and with it every machine loses exactly the 27% the breaker allows and then waits for a human. The evidence window and fresh-evidence options do not change this at real-data false-alarm rates; they only help when false alarms are rare.

Meaning: with the current detector, the system cannot run unattended on real many-metric telemetry. It will request a human within hours of being switched on, on every system tried. This is a consequence of the detector's quality (`BASELINE_COMPARISON.md`), not of the governance logic, which behaved correctly throughout (integrity checks passed in all 36 runs).

### 2. On clean synthetic data the same leak exists in a milder form

30 independent Gaussian metrics, 3,000 clean steps, 8 seeds (false alarms were 0.3% to 0.6% in a separate 1,500-step check): the defaults tighten 3 times in every seed (the breaker cap). Evidence never expires, so any three violations ever are a pattern. With `evidence_window=100` and `require_fresh_evidence` the count falls to 0 or 1 per run (0 in 3 of 8 seeds; a window of 50 gives 0 in 6 of 8). Both options stay **off by default**: turning them on breaks three tests that pin the old behavior on purpose, so the choice is yours. I recommend turning them on.

### 3. Defects found and fixed

- **Warmup at many metrics.** The generative detector locked its model after a fixed 20 observations whatever the metric count. At 30 metrics it alarmed on 48% of the first 100 steps. It now needs 5 observations per metric (at least 20), which brings that to about 1%. Systems with 4 or fewer metrics are unchanged.
- **Hold-log flooding.** After the breaker tripped, every later step added the same "held" record to a list and to the audit log: 7,997 copies in 8,000 real steps. Now one record per hold episode (a new one after an acknowledgement). This also removes an unbounded-memory path (T11) from the breaker.

### 4. T4 (spoofed expected-load patterns): demonstrated, fixed behind an option

- **Demonstrated:** with default settings, anyone who can call the classifier registers a pattern covering their own attack values and the violations are excused; no tightening is proposed.
- **Fix:** `Governor(strict_patterns=True)` makes only patterns registered through `Governor.register_expected_pattern(pattern, operator_id, credential)` count. That call authenticates against the operator registry (when one exists) and writes an audit record with the operator and rationale. A pattern registered directly on the classifier is kept but ignored and listed in `classifier.unapproved`.
- **Left open:** `strict_patterns` is off by default because many existing tests register patterns directly. In the default configuration T4 is still open.

### 5. The controls compose

With the operator registry and audit log on: a flood is stopped at the breaker cap with one hold record; impersonation attempts (wrong secret, another operator's secret, unknown operator, no secret) are all refused and the limit does not move; the real operator can loosen and acknowledge; every step leaves the integrity check passing; and four kinds of tampering (a recorded decision, the version history, a log entry, a consistent rewrite of the whole log) are each caught, the last only against the earlier anchor.

## Remaining issues

1. **The detector's real-data false-alarm rate (2% to 12%) makes unattended operation impossible.** This is the dominant open problem, and the soak is a ready-made yardstick for it: success is fewer unjustified tightenings, not just a lower alarm count.
2. Evidence window and fresh evidence off by default (finding 2); `strict_patterns` off by default (finding 4).
3. The soak does not include the true anomalies' effect: tightenings that follow real anomalies would be justified, and almost none occurred in the window, so the breaker is spent on noise before an attack arrives. A real attack during a held period is not tightened further; an operator has to act.
4. Single boundary per run; one set of 12 machines; the 8,000-step test window is a prefix of each machine's test segment.
