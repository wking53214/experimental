"""
Phase 13 soak: real telemetry through the whole closed loop.

For each Server Machine Dataset machine: a Governor with the default settings (generative detection,
breaker of 3, semantic layer on) ingests the tail of the training segment, then the test segment, one
step at a time. After every step it runs detect_and_propose -> authorize -> apply. Reported per
configuration: alarms, tightenings, the final limit (started at 100), and how many tightenings
happened when no labeled anomaly was within the previous 100 steps ("unjustified").

Configurations:  default | evidence window 100 + fresh evidence | breaker off (what the breaker prevents)
Run:  PYTHONPATH=. python -m experiments.phase13_soak [--machines ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time

import numpy as np

from experiments.real_data_smd import load
from src.governance.governor import Governor

WARM, TEST = 3000, 8000
MACHINES = ["machine-1-1", "machine-2-1", "machine-3-1", "machine-1-6", "machine-1-2", "machine-1-3",
            "machine-1-4", "machine-2-2", "machine-2-3", "machine-2-4", "machine-3-2", "machine-3-3"]
CONFIGS = {
    "default": {},
    "window 100 + fresh": {"evidence_window": 100, "require_fresh_evidence": True},
    "breaker off": {"max_auto_tightenings": None},
}


def run(machine, kw):
    tr, te, lb = load(machine)
    tr, te, lb = tr[-WARM:], te[:TEST], lb[:TEST]
    names = [f"m{i}" for i in range(tr.shape[1])]
    g = Governor(store_path=tempfile.mkdtemp(), **kw)
    g.boundaries.create_boundary("svc", "cpu", 100.0)
    t0 = time.time()
    for i, row in enumerate(tr):
        g.ingest_metrics("svc", float(i), dict(zip(names, map(float, row))))
    alarms = alarms_normal = tight = unjustified = 0
    d = np.diff(np.concatenate([[0], lb.astype(int), [0]]))
    onsets = list(np.where(d == 1)[0])  # labeled anomaly starts inside the test window
    tighten_steps = []
    for i, row in enumerate(te):
        det, _ = g.ingest_metrics("svc", float(WARM + i), dict(zip(names, map(float, row))))
        a = bool(det["anomaly_detected"])
        alarms += a
        alarms_normal += a and lb[i] == 0
        p = g.detect_and_propose_adaptation("svc")
        if p:
            ap, _ = g.authorize_proposal(p)
            g.apply_approved_proposal(ap)
            tight += 1
            tighten_steps.append(i)
            if not lb[max(0, i - 100):i + 1].any():
                unjustified += 1
    ok, checks = g.verify_governance_integrity()
    # criterion 4: did a tightening follow a real anomaly onset within 100 steps?
    responded = [any(o <= t <= o + 100 for t in tighten_steps) for o in onsets]
    return {"anomaly_events": len(onsets), "justified_response": bool(any(responded)),
            "onsets_with_response": int(sum(responded)), "steps": len(te), "alarms": int(alarms), "false_alarms": int(alarms_normal),
            "false_alarm_rate": alarms_normal / max(1, int((lb == 0).sum())),
            "tightenings": tight, "unjustified_tightenings": unjustified,
            "final_limit": round(g.boundaries.get_boundary("svc").current_limit, 2),
            "holds_logged": len(g.tightening_holds), "integrity_ok": bool(ok),
            "audit_entries": len(g.audit.entries), "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machines", nargs="*", default=MACHINES)
    ap.add_argument("--json")
    a = ap.parse_args()
    out = {}
    for m in a.machines:
        out[m] = {}
        for cname, kw in CONFIGS.items():
            out[m][cname] = run(m, kw)
            r = out[m][cname]
            print(f"{m} | {cname:20s} alarms {r['alarms']:5d} (false {r['false_alarm_rate']:.3f})  tightenings {r['tightenings']:2d} "
                  f"(unjustified {r['unjustified_tightenings']:2d})  final limit {r['final_limit']:6.1f}  holds {r['holds_logged']:5d}  "
                  f"integrity {r['integrity_ok']}  {r['seconds']}s", flush=True)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
