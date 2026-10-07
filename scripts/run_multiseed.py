#!/usr/bin/env python3
"""Phase 10A/11C multi-seed runner (stochastic variance).

Statistics: rates (false positives, detection, success) are pooled across seeds and given a
Wilson score interval, which stays inside [0, 1]; continuous values get a Student-t
interval. An experiment whose result is identical on every seed is flagged as
deterministic (more seeds add no information). Each targeted experiment reports whether
the point estimate meets its target AND whether the 95% interval supports it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.governance.anomaly_detector import AdaptiveAnomalyDetector
from src.governance.governor import Governor
from src.governance.authority import AuthorizationResult

# Two-sided 95% Student-t critical values, df = 1..30
T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
       2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
       2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def wilson(successes: int, trials: int, z: float = 1.96) -> dict:
    """Wilson score interval for a proportion (valid near 0 and 1)."""
    if trials <= 0:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0, "trials": 0, "method": "wilson"}
    p = successes / trials
    denom = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return {"mean": p, "ci_low": max(0.0, center - half), "ci_high": min(1.0, center + half),
            "trials": trials, "method": "wilson (pooled over seeds)"}


def t_interval(values) -> dict:
    n = len(values)
    if n == 0:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0, "method": "t"}
    m = statistics.mean(values)
    if n == 1 or statistics.pstdev(values) == 0:
        return {"mean": m, "ci_low": m, "ci_high": m, "n": n, "stdev": 0.0,
                "deterministic": True, "method": "t (no variance across seeds)"}
    sd = statistics.stdev(values)
    half = (T95[n - 2] if n - 1 <= 30 else 1.96) * sd / math.sqrt(n)
    return {"mean": m, "ci_low": m - half, "ci_high": m + half, "n": n, "stdev": sd, "method": "t"}


def rate_summary(pairs) -> dict:
    """pairs: [(successes, trials), ...] per seed."""
    s = wilson(sum(a for a, _ in pairs), sum(b for _, b in pairs))
    s["n"] = len(pairs)
    per_seed = [a / b for a, b in pairs if b]
    if len(set(per_seed)) <= 1:
        s["deterministic"] = True
    return s


def check_target(summary: dict, op: str, target: float):
    """Returns (point_estimate_meets, interval_supports)."""
    m, lo, hi = summary["mean"], summary["ci_low"], summary["ci_high"]
    if m is None:
        return False, False
    if op == ">=":
        return m >= target - 1e-9, lo >= target - 1e-9
    if op == "<=":
        return m <= target + 1e-9, hi <= target + 1e-9
    if op == "<":
        return m < target, hi < target
    raise ValueError(op)


def experiment_7c_diurnal_fp(seed: int):
    rng = random.Random(seed)
    det = AdaptiveAnomalyDetector(f"diurnal_{seed}", window_size=48, learning_window=12)
    anomalies = 0
    n = 48
    day_mean = 44 + rng.uniform(0, 4)
    night_mean = 34 + rng.uniform(0, 4)
    noise = 1.5 + rng.random() * 2.0
    for hour in range(n):
        if 8 <= (hour % 24) <= 18:
            value = day_mean + rng.gauss(0, noise)
        else:
            value = night_mean + rng.gauss(0, noise)
        r = det.detect_anomaly(float(value), hour)
        if r.is_anomaly:
            anomalies += 1
    return anomalies, n


def _spike_detection(seed: int, name: str, lo_sigma: float, hi_sigma: float):
    """Single-metric detector, baseline 40 (sd 1.5), spikes of lo..hi sigma above it."""
    rng = random.Random(seed)
    det = AdaptiveAnomalyDetector(f"{name}_{seed}", window_size=40, learning_window=12)
    for i in range(20):
        det.detect_anomaly(float(40 + rng.gauss(0, 1.5)), i)
    n_attacks = 3 + rng.randint(0, 2)
    detected = 0
    t = 20
    for _ in range(n_attacks):
        for _ in range(rng.randint(1, 3)):
            det.detect_anomaly(40 + rng.gauss(0, 1.0), t)
            t += 1
        spike = 40 + rng.uniform(lo_sigma, hi_sigma) * 1.5
        if det.detect_anomaly(float(spike), t).is_anomaly:
            detected += 1
        t += 1
    return detected, n_attacks


def experiment_7c_attack_detection(seed: int):
    """The original scenario: spikes of 100 + 15..50, i.e. about 50 sigma. Obvious by construction."""
    rng = random.Random(seed)
    det = AdaptiveAnomalyDetector(f"atk_{seed}", window_size=40, learning_window=12)
    for i in range(20):
        det.detect_anomaly(float(40 + rng.gauss(0, 1.5)), i)
    n_attacks = 3 + rng.randint(0, 2)
    detected = 0
    t = 20
    for _ in range(n_attacks):
        for _ in range(rng.randint(1, 3)):
            det.detect_anomaly(40 + rng.gauss(0, 1.0), t)
            t += 1
        spike = 100 + rng.uniform(15, 50)
        if det.detect_anomaly(float(spike), t).is_anomaly:
            detected += 1
        t += 1
    return detected, n_attacks


def experiment_7c_subtle_spikes(seed: int):
    return _spike_detection(seed, "subtle", 6.0, 12.0)


def experiment_7c_slow_ramp(seed: int):
    """Slow ramp: +0.5 sigma per step for 30 steps. One trial per seed: any alarm counts."""
    rng = random.Random(seed)
    det = AdaptiveAnomalyDetector(f"ramp_{seed}", window_size=40, learning_window=12)
    for i in range(20):
        det.detect_anomaly(float(40 + rng.gauss(0, 1.5)), i)
    for k in range(1, 31):
        if det.detect_anomaly(float(40 + 0.5 * 1.5 * k + rng.gauss(0, 1.5)), 19 + k).is_anomaly:
            return 1, 1
    return 0, 1


def experiment_3_late_succeeded(seed: int) -> int:
    rng = random.Random(seed)
    # Reference results (results/multiseed_summary.md) were produced with no tightening limit.
    gov = Governor(store_path=f"/tmp/ms_p3_{seed}_{time.time()}", use_semantic=True,
                   max_auto_tightenings=None)
    for bid in ["cpu_limit", "memory_limit", "io_ops"]:
        gov.boundaries.create_boundary(bid, bid, 100)
        gov.patterns.create_pattern(f"{bid}_p", bid, 2, 60)
    late_succeeded = 0
    boundaries = ["cpu_limit", "memory_limit", "io_ops"]
    for it in range(1, 21):
        bid = boundaries[it % 3]
        b = gov.boundaries.get_boundary(bid)
        target = b.current_limit * (1.15 + rng.random() * 0.15)
        _, viol = gov.execute_against_boundary(bid, target, context={"iteration": it})
        outcome = "succeeded"
        if viol:
            prop = gov.detect_and_propose_adaptation(bid)
            if prop:
                _, res = gov.authorize_proposal(prop)
                if res == AuthorizationResult.AUTO_APPROVED:
                    try:
                        gov.apply_approved_proposal(prop)
                    except ValueError:
                        pass
                outcome = "detected"
            else:
                viols = gov.events.get_violations_for_boundary(bid)
                outcome = "detected" if len(viols) >= 2 else "succeeded"
        if 11 <= it <= 15 and outcome == "succeeded":
            late_succeeded += 1
    assert gov.authority.verify_no_auto_loosen()
    assert gov.authority.verify_no_auto_disable()
    return late_succeeded


def experiment_5_endurance(seed: int) -> dict:
    rng = random.Random(seed)
    gov = Governor(store_path=f"/tmp/ms_p5_{seed}_{time.time()}", use_semantic=True,
                   max_auto_tightenings=None)
    boundaries = [f"e_{i:03d}" for i in range(10)]
    initial = {}
    for bid in boundaries:
        gov.boundaries.create_boundary(bid, "r", 1000)
        initial[bid] = 1000
        gov.patterns.create_pattern(f"{bid}_p", bid, 2, 30)
    succeeded = 0
    total = 1000
    for it in range(total):
        bid = boundaries[it % 10]
        b = gov.boundaries.get_boundary(bid)
        mag = 1.15 + rng.random() * 0.1
        _, viol = gov.execute_against_boundary(bid, b.current_limit * mag)
        if viol:
            prop = gov.detect_and_propose_adaptation(bid)
            if prop:
                _, res = gov.authorize_proposal(prop)
                if res == AuthorizationResult.AUTO_APPROVED:
                    try:
                        gov.apply_approved_proposal(prop)
                    except ValueError:
                        pass
            else:
                if b.current_limit > initial[bid] * 0.25:
                    succeeded += 1
        else:
            succeeded += 1
    assert gov.authority.verify_no_auto_loosen()
    usabilities = []
    for bid in boundaries:
        b = gov.boundaries.get_boundary(bid)
        usabilities.append((b.current_limit / initial[bid]) * 100)
    return {
        "succeeded": succeeded,
        "total": total,
        "success_rate": succeeded / total,
        "min_usability": min(usabilities),
        "avg_usability": statistics.mean(usabilities),
    }


def main():
    ap = argparse.ArgumentParser(description="Phase 10A/11C multi-seed runner")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", type=str, default="results/multiseed_summary.json")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero unless every target is supported by its 95%% interval")
    args = ap.parse_args()
    seeds = list(range(args.seeds))

    results = {"meta": {"seeds": args.seeds, "timestamp": time.time()}, "experiments": {}}
    print(f"Running multi-seed protocol N={args.seeds}...")

    def add(name, summary, op=None, target=None, note=""):
        entry = {"summary": summary, "note": note}
        if op is None:
            entry["target"] = "informational (no target)"
            entry["pass"] = None
            entry["ci_supports_target"] = None
        else:
            met, supported = check_target(summary, op, target)
            entry.update({"target": f"{op} {target}", "pass": met, "ci_supports_target": supported})
        results["experiments"][name] = entry
        tag = " (identical on every seed)" if summary.get("deterministic") else ""
        print(f"  {name}: {summary['mean']:.4f}  95% [{summary['ci_low']:.4f}, {summary['ci_high']:.4f}]{tag}")

    add("7c_diurnal_fp", rate_summary([experiment_7c_diurnal_fp(s) for s in seeds]), "<=", 0.05)
    add("7c_attack_detection", rate_summary([experiment_7c_attack_detection(s) for s in seeds]), ">=", 0.95,
        "spikes about 50 sigma above baseline: obvious by construction")
    add("7c_attack_detection_subtle", rate_summary([experiment_7c_subtle_spikes(s) for s in seeds]),
        note="spikes 6-12 sigma above baseline")
    add("7c_attack_detection_slow_ramp", rate_summary([experiment_7c_slow_ramp(s) for s in seeds]),
        note="+0.5 sigma per step for 30 steps; any alarm counts")
    add("3_late_succeeded", t_interval([experiment_3_late_succeeded(s) for s in seeds]), "<=", 1)
    end_results = [experiment_5_endurance(s) for s in seeds]
    add("5_endurance_success_rate", rate_summary([(r["succeeded"], r["total"]) for r in end_results]), "<", 0.15)
    add("5_endurance_min_usability", t_interval([r["min_usability"] for r in end_results]), ">=", 20.0)

    targeted = [e for e in results["experiments"].values() if e["pass"] is not None]
    all_pass = all(e["pass"] for e in targeted)
    supported = sum(1 for e in targeted if e["ci_supports_target"])
    results["meta"].update({"targets_met_point_estimate": all_pass,
                            "targets_supported_by_interval": f"{supported}/{len(targeted)}"})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))

    md = out.with_suffix(".md")
    lines = [
        "# Phase 10A Multi-Seed Summary",
        "",
        f"N = {args.seeds} seeds. Rates are pooled across seeds with a Wilson 95% interval; "
        "continuous values use a t interval.",
        "",
        "| Experiment | Result | 95% interval | Target | Point estimate meets | Interval supports | Note |",
        "|------------|--------|--------------|--------|----------------------|-------------------|------|",
    ]
    for name, e in results["experiments"].items():
        s = e["summary"]
        det = " (identical on every seed)" if s.get("deterministic") else ""
        yn = lambda v: "n/a" if v is None else ("yes" if v else "no")
        lines.append(f"| {name} | {s['mean']:.4f} | [{s['ci_low']:.4f}, {s['ci_high']:.4f}]{det} | "
                     f"{e['target']} | {yn(e['pass'])} | {yn(e['ci_supports_target'])} | {e['note']} |")
    lines.append("")
    lines.append(f"**Targets met by point estimate:** {'YES' if all_pass else 'NO'} | "
                 f"**supported by the 95% interval:** {supported}/{len(targeted)}")
    md.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out} and {md}")
    print(("ALL TARGETS MET (point estimates)" if all_pass else "SOME TARGETS MISSED")
          + f"; interval supports {supported}/{len(targeted)}")
    if not all_pass:
        return 1
    if args.strict and supported < len(targeted):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
