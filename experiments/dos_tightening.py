"""
Experiment B: tightening as a denial-of-service lever.

An attacker who can only feed observed values (no operator access) sends values just over the
boundary's current limit. The system correctly tightens, so the attacker walks the boundary
down to the 20% usability floor. Legitimate traffic (~N(50, 5) against an initial limit of 100)
starts being blocked once the limit nears 60.

Measured per defence: attacker values needed to reach the floor, simulated time, and
how much of the legitimate traffic is blocked at the end, plus whether a patient attacker
(one value per `patience_s`) still gets there.
Run:  PYTHONPATH=. python -m experiments.dos_tightening [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import tempfile

import numpy as np

from src.governance.governor import Governor

LEGIT_MEAN, LEGIT_SD, INITIAL = 50.0, 5.0, 100.0
CONFIGS = {
    "none (current default)": {},
    "fresh evidence": {"require_fresh_evidence": True},
    "fresh + cooldown 1h": {"require_fresh_evidence": True, "tighten_cooldown_s": 3600.0},
    "fresh + breaker (max 3)": {"require_fresh_evidence": True, "max_auto_tightenings": 3},
    "fresh + cooldown + breaker": {"require_fresh_evidence": True, "tighten_cooldown_s": 3600.0,
                                   "max_auto_tightenings": 3},
}


def legit_blocked(limit):
    # exact normal tail probability for the legitimate workload
    from math import erf, sqrt
    return 0.5 * (1.0 - erf((limit - LEGIT_MEAN) / (LEGIT_SD * sqrt(2.0))))


def run(cfg, spacing_s, max_events=2000, horizon_s=30 * 86400):
    now = [0.0]
    g = Governor(store_path=tempfile.mkdtemp(), clock=lambda: now[0], **cfg)
    g.boundaries.create_boundary("api", "rate", INITIAL)
    events = 0
    floor_at = None
    t_first_harm = None
    while events < max_events and now[0] < horizon_s:
        lim = g.boundaries.get_boundary("api").current_limit
        g.execute_against_boundary("api", lim * 1.05)
        events += 1
        p = g.detect_and_propose_adaptation("api")
        if p:
            p2, _ = g.authorize_proposal(p)
            g.apply_approved_proposal(p2)
        lim = g.boundaries.get_boundary("api").current_limit
        if t_first_harm is None and legit_blocked(lim) > 0.01:
            t_first_harm = (events, now[0])
        if floor_at is None and lim < INITIAL * 0.25:
            floor_at = (events, now[0])
            break
        now[0] += spacing_s
    lim = g.boundaries.get_boundary("api").current_limit
    return {
        "events_to_harm": t_first_harm[0] if t_first_harm else None,
        "events_to_floor": floor_at[0] if floor_at else None,
        "days_to_floor": round(floor_at[1] / 86400, 2) if floor_at else None,
        "final_limit": round(lim, 2),
        "legit_blocked_final": round(legit_blocked(lim), 3),
        "holds_logged": len(g.tightening_holds),
        "integrity_ok": all(ok for _, ok in g.verify_governance_integrity()[1]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    out = {}
    for name, cfg in CONFIGS.items():
        out[name] = {"fast (1 value/min)": run(cfg, 60.0, max_events=5000, horizon_s=3 * 86400),
                     "patient (1 value/hour)": run(cfg, 3600.0, max_events=5000, horizon_s=30 * 86400)}
    print(json.dumps(out, indent=2))
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
