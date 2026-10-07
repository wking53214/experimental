"""Statistics and smoke test for scripts/run_multiseed.py."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.run_multiseed as ms

ROOT = Path(__file__).resolve().parents[1]


class TestWilson:
    def test_known_value(self):
        s = ms.wilson(38, 40)
        assert s["ci_low"] == pytest.approx(0.8350, abs=1e-3)
        assert s["ci_high"] == pytest.approx(0.9862, abs=1e-3)

    @pytest.mark.parametrize("k,n", [(0, 10), (10, 10), (0, 1), (1, 1), (5, 1000)])
    def test_interval_stays_inside_zero_one_and_contains_the_estimate(self, k, n):
        s = ms.wilson(k, n)
        assert 0.0 <= s["ci_low"] <= s["mean"] <= s["ci_high"] <= 1.0

    def test_no_trials(self):
        assert ms.wilson(0, 0)["mean"] is None

    def test_interval_narrows_with_more_trials(self):
        a, b = ms.wilson(95, 100), ms.wilson(950, 1000)
        assert (b["ci_high"] - b["ci_low"]) < (a["ci_high"] - a["ci_low"])


class TestTInterval:
    def test_identical_values_are_flagged_deterministic(self):
        s = ms.t_interval([20.5, 20.5, 20.5])
        assert s["deterministic"] is True and s["ci_low"] == s["ci_high"] == 20.5

    def test_varying_values_get_a_real_interval(self):
        s = ms.t_interval([1.0, 2.0, 3.0, 4.0])
        assert s["ci_low"] < 2.5 < s["ci_high"] and not s.get("deterministic")

    def test_small_samples_use_the_wider_t_value(self):
        # n=2: t = 12.706 (not 1.96)
        s = ms.t_interval([0.0, 2.0])
        assert (s["ci_high"] - s["mean"]) == pytest.approx(12.706 * 1.4142135 / 1.4142135, rel=1e-3)


class TestTargets:
    def test_point_estimate_can_meet_a_target_the_interval_does_not_support(self):
        s = ms.wilson(38, 40)  # 0.95
        met, supported = ms.check_target(s, ">=", 0.95)
        assert met is True and supported is False

    def test_upper_bound_targets(self):
        assert ms.check_target(ms.wilson(1, 500), "<=", 0.05) == (True, True)
        # 1 in 100 meets 5% as a point estimate, but its upper bound (about 5.4%) does not
        assert ms.check_target(ms.wilson(1, 100), "<=", 0.05) == (True, False)
        assert ms.check_target(ms.wilson(5, 100), "<=", 0.05)[1] is False

    def test_rate_summary_flags_seed_independent_results(self):
        assert ms.rate_summary([(3, 10), (3, 10)])["deterministic"] is True
        assert "deterministic" not in ms.rate_summary([(3, 10), (4, 10)])


def test_script_runs_and_reports_interval_support(tmp_path):
    out = tmp_path / "ms.json"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/run_multiseed.py"),
                           "--seeds", "3", "--out", str(out)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out.read_text())
    exps = data["experiments"]
    assert exps["7c_attack_detection_subtle"]["pass"] is None  # informational
    for name in ("7c_diurnal_fp", "7c_attack_detection", "5_endurance_success_rate"):
        s = exps[name]["summary"]
        assert 0.0 <= s["ci_low"] <= s["mean"] <= s["ci_high"] <= 1.0
        assert "ci_supports_target" in exps[name]
    assert "targets_supported_by_interval" in data["meta"]
    assert out.with_suffix(".md").exists()


def test_strict_mode_fails_when_an_interval_does_not_support_its_target(tmp_path):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/run_multiseed.py"), "--seeds", "3",
                           "--strict", "--out", str(tmp_path / "s.json")], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1  # with 3 seeds no interval is tight enough to support the targets
