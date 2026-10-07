"""
Baselines versus the project's detectors on the synthetic evolved-attacker experiment, with the
baselines run at several false-alarm rates so detection can be compared at matched FPR.
Run:  PYTHONPATH=. python -m experiments.baseline_comparison [--seeds 5] [--json out.json]
"""
import argparse
import json

from experiments.phase9_end_to_end import e1

VARIANTS = [
    ("z-score @0.1%", "Z"), ("z-score @1%", "Z1"), ("z-score @4%", "Z4"),
    ("mahalanobis @0.1%", "M"), ("mahalanobis @1%", "M1"), ("mahalanobis @4%", "M4"),
    ("generative(default)", "Gc"), ("hybrid(default)", "H"), ("hybrid+temporal", "HT"),
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--json")
    a = ap.parse_args()
    res = e1(a.seeds, VARIANTS)
    print(f"{'detector':22s} stepFPR  det_all  det_last  evasive  bestUndetDamage")
    for n, r in res.items():
        print(f"{n:22s} {r['step_fpr']:.3f}   {r['det_rate_all']:.2f}    {r['det_rate_last']:.2f}     {r['evasive_attacks']:5.0f}   {r['best_undetected_damage']:.2f}")
    if a.json:
        json.dump(res, open(a.json, "w"), indent=2)
