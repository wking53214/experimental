# Phase 10A Multi-Seed Summary

N = 200 seeds. Rates are pooled across seeds with a Wilson 95% interval; continuous values use a t interval.

| Experiment | Result | 95% interval | Target | Point estimate meets | Interval supports | Note |
|------------|--------|--------------|--------|----------------------|-------------------|------|
| 7c_diurnal_fp | 0.0091 | [0.0074, 0.0112] | <= 0.05 | yes | yes |  |
| 7c_attack_detection | 0.9526 | [0.9353, 0.9654] | >= 0.95 | yes | no | spikes about 50 sigma above baseline: obvious by construction |
| 7c_attack_detection_subtle | 0.8372 | [0.8096, 0.8614] | informational (no target) | n/a | n/a | spikes 6-12 sigma above baseline |
| 7c_attack_detection_slow_ramp | 1.0000 | [0.9812, 1.0000] (identical on every seed) | informational (no target) | n/a | n/a | +0.5 sigma per step for 30 steps; any alarm counts |
| 3_late_succeeded | 0.0000 | [0.0000, 0.0000] (identical on every seed) | <= 1 | yes | yes |  |
| 5_endurance_success_rate | 0.0100 | [0.0096, 0.0104] (identical on every seed) | < 0.15 | yes | yes |  |
| 5_endurance_min_usability | 20.5891 | [20.5891, 20.5891] (identical on every seed) | >= 20.0 | yes | yes |  |

**Targets met by point estimate:** YES | **supported by the 95% interval:** 4/5
