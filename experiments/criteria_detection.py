"""
Criterion 2: on the 8 held-out SMD machines, is the default detector's point recall at least a plain
Mahalanobis baseline's when both have the same false-alarm rate? The baseline's threshold is set from the
test normals to match the detector's measured false-alarm rate (an oracle that favors the baseline).
Uses raw alarms cached by experiments/persistence_filter.py --cache.
Run:  PYTHONPATH=. python -m experiments.criteria_detection [--json out.json]
"""
import argparse
import json

import numpy as np

from experiments.baselines import MahalanobisBaseline
from experiments.persistence_filter import CACHE, HELDOUT
from experiments.real_data_smd import baseline_scores, load


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    out = {}
    print(f"{'machine':12s} FPR(detector)  recall(detector)  recall(baseline @ same FPR)")
    for m in HELDOUT:
        z = np.load(f"{CACHE}/{m}.npz")
        al, lb = z["alarms"], z["labels"].astype(int)
        normal = lb == 0
        fpr = float(al[normal].mean())
        tr, te, _ = load(m)
        s, _ = baseline_scores(MahalanobisBaseline, tr, te)
        thr = np.quantile(s[normal], 1.0 - fpr)
        b = s > thr
        out[m] = {"fpr": fpr, "detector_recall": float(al[~normal].mean()), "baseline_recall": float(b[~normal].mean())}
        print(f"{m:12s} {fpr:11.3f}   {out[m]['detector_recall']:14.2f}   {out[m]['baseline_recall']:.2f}")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
