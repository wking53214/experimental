#!/usr/bin/env python3
"""Phase 10A/11C multi-seed runner (stochastic variance)."""
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


def mean_ci(values, confidence=0.95):
    n = len(values)
    if n == 0:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0}
    m = statistics.mean(values)
    if n == 1:
        return {"mean": m, "ci_low": m, "ci_high": m, "n": 1}
    sd = statistics.stdev(values)
    z = 1.96
    half = z * sd / math.sqrt(n)
    return {"mean": m, "ci_low": m - half, "ci_high": m + half, "n": n, "stdev": sd}


def experiment_7c_diurnal_fp(seed: int) -> float:
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
    return anomalies / n


def experiment_7c_attack_detection(seed: int) -> float:
    rng = random.Random(seed)
    det = AdaptiveAnomalyDetector(f"atk_{seed}", window_size=40, learning_window=12)
    for i in range(20):
        val = 40 + rng.gauss(0, 1.5)
        det.detect_anomaly(float(val), i)
    n_attacks = 3 + rng.randint(0, 2)
    detected = 0
    total = 0
    t = 20
    for k in range(n_attacks):
        for _ in range(rng.randint(1, 3)):
            det.detect_anomaly(40 + rng.gauss(0, 1.0), t)
            t += 1
        spike = 100 + rng.uniform(15, 50)
        r = det.detect_anomaly(float(spike), t)
        t += 1
        total += 1
        if r.is_anomaly:
            detected += 1
    return detected / total if total else 0.0


def experiment_3_late_succeeded(seed: int) -> int:
    rng = random.Random(seed)
    gov = Governor(store_path=f"/tmp/ms_p3_{seed}_{time.time()}", use_semantic=True)
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
    gov = Governor(store_path=f"/tmp/ms_p5_{seed}_{time.time()}", use_semantic=True)
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
        "success_rate": succeeded / total,
        "min_usability": min(usabilities),
        "avg_usability": statistics.mean(usabilities),
    }


def main():
    ap = argparse.ArgumentParser(description="Phase 10A/11C multi-seed runner")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", type=str, default="results/multiseed_summary.json")
    args = ap.parse_args()
    seeds = list(range(args.seeds))

    results = {
        "meta": {"seeds": args.seeds, "timestamp": time.time()},
        "experiments": {},
    }

    print(f"Running multi-seed protocol N={args.seeds}...")

    fp_rates = [experiment_7c_diurnal_fp(s) for s in seeds]
    results["experiments"]["7c_diurnal_fp"] = {
        "values": fp_rates,
        "summary": mean_ci(fp_rates),
        "target": "mean <= 0.05",
        "pass": mean_ci(fp_rates)["mean"] <= 0.05 + 1e-9,
    }
    print(f"  7C diurnal FP: mean={results['experiments']['7c_diurnal_fp']['summary']['mean']:.4f}")

    det_rates = [experiment_7c_attack_detection(s) for s in seeds]
    results["experiments"]["7c_attack_detection"] = {
        "values": det_rates,
        "summary": mean_ci(det_rates),
        "target": "mean >= 0.95",
        "pass": mean_ci(det_rates)["mean"] >= 0.95 - 1e-9,
    }
    print(f"  7C attack det: mean={results['experiments']['7c_attack_detection']['summary']['mean']:.4f}")

    late = [experiment_3_late_succeeded(s) for s in seeds]
    results["experiments"]["3_late_succeeded"] = {
        "values": late,
        "summary": mean_ci(late),
        "target": "mean <= 1",
        "pass": mean_ci(late)["mean"] <= 1 + 1e-9,
    }
    print(f"  P3 late SUCCEEDED: mean={results['experiments']['3_late_succeeded']['summary']['mean']:.4f}")

    end_results = [experiment_5_endurance(s) for s in seeds]
    success_rates = [r["success_rate"] for r in end_results]
    min_usability = [r["min_usability"] for r in end_results]
    results["experiments"]["5_endurance_success_rate"] = {
        "values": success_rates,
        "summary": mean_ci(success_rates),
        "target": "mean < 0.15",
        "pass": mean_ci(success_rates)["mean"] < 0.15,
    }
    results["experiments"]["5_endurance_min_usability"] = {
        "values": min_usability,
        "summary": mean_ci(min_usability),
        "target": "mean >= 20",
        "pass": mean_ci(min_usability)["mean"] >= 20 - 1e-9,
    }
    print(f"  P5 success rate: mean={results['experiments']['5_endurance_success_rate']['summary']['mean']:.4f}")
    print(f"  P5 min usability: mean={results['experiments']['5_endurance_min_usability']['summary']['mean']:.2f}%")

    all_pass = all(exp["pass"] for exp in results["experiments"].values())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))

    md = out.with_suffix(".md")
    lines = [
        "# Phase 10A Multi-Seed Summary",
        "",
        f"N = {args.seeds} seeds",
        "",
        "| Experiment | Mean | 95% CI | Target | Pass |",
        "|------------|------|--------|--------|------|",
    ]
    for name, exp in results["experiments"].items():
        s = exp["summary"]
        lines.append(
            f"| {name} | {s['mean']:.4f} | [{s['ci_low']:.4f}, {s['ci_high']:.4f}] | {exp['target']} | {'OK' if exp['pass'] else 'FAIL'} |"
        )
    lines.append("")
    lines.append(f"**All targets met:** {'YES' if all_pass else 'NO'}")
    md.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out} and {md}")
    print("ALL TARGETS MET" if all_pass else "SOME TARGETS MISSED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
