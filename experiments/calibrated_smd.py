"""
Does an empirically calibrated Mahalanobis detector (src/governance/calibrated_detector.py) hold its
intended false-alarm rate on real telemetry, where Gaussian-formula thresholds did not?

Protocol (fixed before looking at held-out machines):
  DEV machines are used to choose a configuration. HELDOUT machines are never used to choose anything.
  Target false-alarm rate 1%. Selection rule: among configurations with mean dev event recall >= 0.9,
  the one with the smallest mean |log10(realized FPR / target)| on the dev machines.
All configurations, selected or not, are reported on both sets.
Run:  PYTHONPATH=. python -m experiments.calibrated_smd [--json out.json]
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from experiments.baselines import MahalanobisBaseline
from experiments.real_data_smd import baseline_scores, load, stream, summarize
from src.governance.calibrated_detector import CalibratedMahalanobisDetector

DEV = ["machine-1-1", "machine-2-1", "machine-3-1", "machine-1-6"]
HELDOUT = ["machine-1-2", "machine-1-3", "machine-1-4", "machine-2-2",
           "machine-2-3", "machine-2-4", "machine-3-2", "machine-3-3"]
TARGET = 0.01
CONFIGS = {  # name -> (decay, reject_mult)
    "frozen": (0.0, 1.0),
    "adapt 0.002, unflagged only": (0.002, 1.0),
    "adapt 0.01, unflagged only": (0.01, 1.0),
    "adapt 0.002, up to 2x threshold": (0.002, 2.0),
    "adapt 0.01, up to 2x threshold": (0.01, 2.0),
}


def run(m):
    tr, te, lb = load(m)
    out = {}
    for name, (decay, rm) in CONFIGS.items():
        d = CalibratedMahalanobisDetector(target_fpr=TARGET, decay=decay, reject_mult=rm).fit(tr)
        out[name] = summarize([d.process(x) for x in te], lb)
    s, thr = baseline_scores(MahalanobisBaseline, tr, te)
    out["reference: Mahalanobis, chi-square cutoff"] = summarize(s > thr, lb)
    out["reference: generative detector"] = summarize(stream("Gc", tr, te), lb)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    res = {}
    for m in DEV + HELDOUT:
        res[m] = run(m)
        print(m, "done", flush=True)
    names = list(res[DEV[0]])

    def agg(machines, name):
        v = [res[m][name] for m in machines]
        fpr = [x["step_fpr"] for x in v]
        return {"fpr_mean": float(np.mean(fpr)), "fpr_median": float(np.median(fpr)),
                "fpr_ratio_log10_abs": float(np.mean([abs(np.log10(max(f, 1e-4) / TARGET)) for f in fpr])),
                "within_3x": int(sum(f <= 3 * TARGET for f in fpr)),
                "event_rec": float(np.mean([x["event_rec"] for x in v])),
                "point_rec": float(np.mean([x["point_rec"] for x in v]))}

    summary = {"dev": {n: agg(DEV, n) for n in names}, "heldout": {n: agg(HELDOUT, n) for n in names}}
    eligible = [n for n in CONFIGS if summary["dev"][n]["event_rec"] >= 0.9]
    selected = min(eligible, key=lambda n: summary["dev"][n]["fpr_ratio_log10_abs"]) if eligible else None
    summary["selected_on_dev"] = selected
    for part in ("dev", "heldout"):
        print(f"\n{part.upper()}  (target FPR {TARGET:.0%}; machines {len(DEV if part == 'dev' else HELDOUT)})")
        print(f"{'detector':46s} FPR mean  FPR med  within3x  event  point")
        for n in names:
            r = summary[part][n]
            star = " *" if n == selected else ""
            print(f"{n + star:46s} {r['fpr_mean']:.3f}    {r['fpr_median']:.3f}   {r['within_3x']:>3d}      {r['event_rec']:.2f}   {r['point_rec']:.2f}")
    if a.json:
        json.dump({"per_machine": res, "summary": summary}, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
