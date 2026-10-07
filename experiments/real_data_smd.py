"""
Real-data check on the Server Machine Dataset (SMD; 38 server metrics per machine, labeled
anomaly segments). Fetch with scripts/fetch_smd.sh.

Protocol: drop metrics that are constant in the training segment (same for every detector); feed the
training segment as the baseline; stream the test segment one step at a time. Report, on test:
  step_fpr   alarms / steps outside labeled anomalies
  event_rec  labeled anomaly segments with at least one alarm inside / segments
  point_rec  alarmed steps inside anomalies / anomaly steps
Baselines (z-score, Mahalanobis) are scored vectorised and reported at their default operating point
(nominal 0.1% per step, fitted on train) and at the generative detector's measured test FPR ("matched"), where the
threshold comes from the test normals: an oracle for comparing detection power at equal FPR.
Run:  PYTHONPATH=. python -m experiments.real_data_smd [--machines machine-1-1 ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from experiments.baselines import MahalanobisBaseline, ZScoreBaseline
from src.governance.metrics import DetectorPipeline
from src.governance.multivariate import GenerativeAnomalyDetector
from src.governance.phase9_integration import HybridDetectorPipeline

DEFAULT_MACHINES = ["machine-1-1", "machine-2-1", "machine-3-1", "machine-1-6"]


def load(m):
    tr = np.loadtxt(f"data/smd/train_{m}.txt", delimiter=",")
    te = np.loadtxt(f"data/smd/test_{m}.txt", delimiter=",")
    lb = np.loadtxt(f"data/smd/test_label_{m}.txt").astype(int)
    keep = tr.std(0) > 1e-9
    return tr[:, keep], te[:, keep], lb


def segments(lb):
    d = np.diff(np.concatenate([[0], lb, [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def summarize(alarms, lb):
    alarms = np.asarray(alarms, dtype=bool)
    segs = segments(lb)
    normal = lb == 0
    return {
        "step_fpr": float(alarms[normal].mean()),
        "event_rec": float(np.mean([alarms[s:e].any() for s, e in segs])),
        "point_rec": float(alarms[~normal].mean()),
        "events": len(segs),
    }


def stream(detector_kind, tr, te):
    names = [f"m{i}" for i in range(tr.shape[1])]
    rec = lambda row: {n: float(v) for n, v in zip(names, row)}
    if detector_kind == "Gc":
        d = GenerativeAnomalyDetector("b", 20)
        for row in tr:
            d.ingest_observation(rec(row))
        out = []
        for row in te:
            o = rec(row)
            out.append(bool(d.detect_anomaly(o)["anomaly_detected"]))
            d.ingest_observation(o)
        return out
    d = {"T": lambda: DetectorPipeline("b", 100),
         "H": lambda: HybridDetectorPipeline("b", 100),
         "HT": lambda: HybridDetectorPipeline("b", 100, temporal_shift=True)}[detector_kind]()
    t = 0.0
    for row in tr:
        d.ingest_metrics(t, rec(row)); t += 1.0
    out = []
    for row in te:
        d.ingest_metrics(t, rec(row)); t += 1.0
        out.append(bool(d.detect_anomalies()["anomaly_detected"]))
    return out


def baseline_scores(cls, tr, te):
    names = [f"m{i}" for i in range(tr.shape[1])]
    b = cls(names, fit_after=len(tr))
    for row in tr:
        b.ingest({n: v for n, v in zip(names, row)})
    return np.array([b.score(r) for r in te]), b.threshold


def run_machine(m, kinds):
    tr, te, lb = load(m)
    res = {"steps": len(te), "metrics_used": tr.shape[1], "anomaly_fraction": float(lb.mean())}
    normal = lb == 0
    for kind in kinds:
        t0 = time.time()
        res[kind] = summarize(stream(kind, tr, te), lb)
        res[kind]["seconds"] = round(time.time() - t0, 1)
    matched_to = res["Gc"]["step_fpr"] if "Gc" in res else 0.05
    for name, cls in (("z-score", ZScoreBaseline), ("mahalanobis", MahalanobisBaseline)):
        s, thr = baseline_scores(cls, tr, te)
        res[f"{name} (default 0.1%)"] = summarize(s > thr, lb)
        for fpr in sorted({0.01, 0.05, round(matched_to, 4)}):
            thr_m = np.quantile(s[normal], 1 - fpr) if fpr > 0 else s.max()
            label = f"{name} (matched {fpr:.1%})" if fpr == round(matched_to, 4) else f"{name} (FPR {fpr:.0%})"
            res[label] = summarize(s > thr_m, lb)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--machines", nargs="*", default=DEFAULT_MACHINES)
    ap.add_argument("--kinds", nargs="*", default=["T", "Gc", "H", "HT"])
    ap.add_argument("--json")
    a = ap.parse_args()
    allr = {}
    for m in a.machines:
        allr[m] = run_machine(m, a.kinds)
        print(f"\n{m}: {allr[m]['steps']} steps, {allr[m]['metrics_used']} metrics, "
              f"{allr[m]['anomaly_fraction']:.1%} anomalous")
        for k, v in allr[m].items():
            if isinstance(v, dict):
                print(f"  {k:30s} fpr {v['step_fpr']:.3f}  event_rec {v['event_rec']:.2f}  point_rec {v['point_rec']:.2f}")
    if a.json:
        json.dump(allr, open(a.json, "w"), indent=2)
