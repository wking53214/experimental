"""
T2: baseline poisoning. An attacker present while the baseline is learned shapes what later counts
as normal. How much does that weaken detection of the attack they launch afterwards?

Synthetic system as in phase9_end_to_end (5 correlated Gaussian metrics, 10% CV), baseline of 200
steps. Poison types, all aimed at `latency` (the metric the later attack shifts):
  contamination p : a fraction p of baseline steps have latency shifted by +3 sd (the same direction
                    and size as the later attack), at random positions
  ramp            : latency mean climbs linearly from 0 to +3 sd across the baseline ("boiling frog")
  inflate k       : latency noise multiplied by k on every baseline step
Probe: 30 clean steps, then latency +s sd for 20 steps, s in {2, 3, 5}. A probe is detected if any
alarm falls inside the attack window; the same window in a clean control episode gives the chance
floor, and "excess" = detected - chance. Wilson 95% intervals over reps.

Run:  PYTHONPATH=. python -m experiments.baseline_poisoning [--reps 100] [--json out.json]
"""
from __future__ import annotations

import argparse
import copy
import json

import numpy as np

from experiments.baselines import MahalanobisBaseline, ZScoreBaseline
from experiments.phase9_end_to_end import COV, METRICS, MEANS, SD
from src.governance.calibrated_detector import CalibratedMahalanobisDetector
from src.governance.multivariate import GenerativeAnomalyDetector

BASELINE, CLEAN_STEPS, ATTACK_STEPS = 200, 30, 20
LAT = METRICS.index("latency")
POISONS = [("none", None), ("contaminate 5%", ("c", 0.05)), ("contaminate 10%", ("c", 0.10)),
           ("contaminate 20%", ("c", 0.20)), ("contaminate 40%", ("c", 0.40)),
           ("ramp to +3sd", ("r", 3.0)), ("inflate noise x2", ("i", 2.0)), ("inflate noise x3", ("i", 3.0))]
PROBES = (2.0, 3.0, 5.0)


def draw(rng, n):
    return rng.multivariate_normal(MEANS, COV, size=n)


def poisoned_baseline(rng, poison):
    X = draw(rng, BASELINE)
    if poison is None:
        return X
    kind, v = poison
    if kind == "c":
        idx = rng.choice(BASELINE, size=int(round(v * BASELINE)), replace=False)
        X[idx, LAT] += 3.0 * SD[LAT]
    elif kind == "r":
        X[:, LAT] += np.linspace(0.0, v, BASELINE) * SD[LAT]
    elif kind == "i":
        X[:, LAT] = MEANS[LAT] + (X[:, LAT] - MEANS[LAT]) * v
    return X


class Adapter:
    """Uniform: fit(X) then process(row) -> alarm."""

    def __init__(self, name):
        self.name = name

    def fit(self, X):
        n = self.name
        if n == "z-score (frozen)":
            self.d = ZScoreBaseline(METRICS, fit_after=len(X))
            for r in X:
                self.d.ingest(dict(zip(METRICS, r)))
        elif n == "mahalanobis (frozen)":
            self.d = MahalanobisBaseline(METRICS, fit_after=len(X))
            for r in X:
                self.d.ingest(dict(zip(METRICS, r)))
        elif n == "generative (online)":
            self.d = GenerativeAnomalyDetector("b", 20)
            for r in X:
                self.d.ingest_observation(dict(zip(METRICS, r)))
        elif n == "calibrated (frozen)":
            self.d = CalibratedMahalanobisDetector(target_fpr=0.01).fit(X)
        elif n == "calibrated + trim 10%":
            self.d = CalibratedMahalanobisDetector(target_fpr=0.01, trim=0.10).fit(X)
        elif n == "calibrated + trim 25%":
            self.d = CalibratedMahalanobisDetector(target_fpr=0.01, trim=0.25).fit(X)
        return self

    def process(self, row):
        n = self.name
        if n in ("z-score (frozen)", "mahalanobis (frozen)"):
            return self.d.ingest(dict(zip(METRICS, row)))
        if n == "generative (online)":
            o = dict(zip(METRICS, row))
            a = bool(self.d.detect_anomaly(o)["anomaly_detected"])
            self.d.ingest_observation(o)
            return a
        return self.d.process(row)


DETECTORS = ["z-score (frozen)", "mahalanobis (frozen)", "generative (online)",
             "calibrated (frozen)", "calibrated + trim 10%", "calibrated + trim 25%"]


def window_alarm(det, rng, shift_sd):
    d = copy.deepcopy(det)
    for row in draw(rng, CLEAN_STEPS):
        d.process(row)
    hit = False
    for row in draw(rng, ATTACK_STEPS):
        row = row.copy()
        row[LAT] += shift_sd * SD[LAT]
        hit |= bool(d.process(row))
    return hit


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def run(reps, seed=0):
    out = {}
    for pname, poison in POISONS:
        for dname in DETECTORS:
            hits = {s: 0 for s in PROBES}
            ctrl_hits = 0
            clean_alarms = clean_steps = 0
            for rep in range(reps):
                rng = np.random.default_rng(seed * 100003 + rep)
                X = poisoned_baseline(rng, poison)
                det = Adapter(dname).fit(X)
                for s in PROBES:
                    hits[s] += window_alarm(det, np.random.default_rng(rep * 7 + int(s)), s)
                ctrl_hits += window_alarm(det, np.random.default_rng(rep * 7 + 99), 0.0)
                d2 = copy.deepcopy(det)
                for row in draw(np.random.default_rng(rep * 7 + 55), 200):
                    clean_alarms += bool(d2.process(row))
                    clean_steps += 1
            out[(pname, dname)] = {
                "step_fpr": clean_alarms / clean_steps,
                "chance_floor": ctrl_hits / reps,
                **{f"det_{s:g}sd": hits[s] / reps for s in PROBES},
                **{f"ci_{s:g}sd": wilson(hits[s], reps) for s in PROBES},
            }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=100)
    ap.add_argument("--json")
    ap.add_argument("--diagnostic", action="store_true", help="only measure check_baseline")
    a = ap.parse_args()
    if a.diagnostic:
        d = diagnostic_power(a.reps)
        print(f"{'baseline':18s} flagged   95% CI          tail  shift  half")
        for k, v in d.items():
            print(f"{k:18s} {v['flagged']:.2f}     [{v['ci'][0]:.2f},{v['ci'][1]:.2f}]   {v['by_tail']:.2f}  {v['by_shift']:.2f}   {v['by_half']:.2f}")
        if a.json:
            json.dump(d, open(a.json, "w"), indent=2)
        return
    res = run(a.reps)
    print(f"{'poison':18s} {'detector':24s} FPR    chance  det@2sd det@3sd det@5sd")
    for (pn, dn), r in res.items():
        print(f"{pn:18s} {dn:24s} {r['step_fpr']:.3f}  {r['chance_floor']:.2f}    {r['det_2sd']:.2f}    {r['det_3sd']:.2f}    {r['det_5sd']:.2f}")
    if a.json:
        json.dump({f"{p} | {d}": v for (p, d), v in res.items()}, open(a.json, "w"), indent=2)



def diagnostic_power(reps=200):
    """How often check_baseline flags each kind of baseline (clean = false-flag rate)."""
    from src.governance.baseline_check import check_baseline
    out = {}
    for pname, poison in POISONS:
        flagged = 0
        by = {"tail": 0, "shift": 0, "half": 0}
        for rep in range(reps):
            rng = np.random.default_rng(555_000 + rep)
            r = check_baseline(poisoned_baseline(rng, poison), sims=150, seed=rep)
            flagged += r["suspicious"]
            for f in r["flags"]:
                by[f] += 1
        out[pname] = {"flagged": flagged / reps, "ci": wilson(flagged, reps), **{f"by_{k}": v / reps for k, v in by.items()}}
    return out


if __name__ == "__main__":
    main()
