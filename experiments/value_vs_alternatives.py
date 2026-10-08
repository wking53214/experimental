"""
Criterion 5: does the adaptive system beat the simple alternatives?

Model (synthetic, deliberately simple): one key whose usage is normally 50 +/- 5 with a limit of 100.
Usage above the limit is shed (admitted = min(usage, limit)). At step 300 the key is compromised or
the agent runs away: usage rises by `excess` for a while. Damage = sum of admitted usage above 50.
Four metrics reach the detector each step (usage, cpu, latency, error rate), correlated with usage.

Systems:
  static          limit stays 100
  adaptive        default Governor (generative detection, breaker of 3): propose -> authorize -> apply
  adaptive+window Governor(evidence_window=100, require_fresh_evidence=True)
  human D         the same detector raises the alarm; a person with PERFECT discrimination (never acts on a
                  false alarm) sets the limit to 60 D steps after the first three alarms during the attack
  oracle          limit 60 the moment the attack starts (the most any tightening could do here)

Attack-free runs measure the cost: legitimate usage shed (in attack runs the shed usage includes the attacker's), and tightenings that should not happen.
Run:  PYTHONPATH=. python -m experiments.value_vs_alternatives [--seeds 30] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import tempfile

import numpy as np

from src.governance.governor import Governor

WARM, ATTACK_AT, STEPS = 300, 300, 900
BASE, SD, LIMIT0, HUMAN_LIMIT = 50.0, 5.0, 100.0, 60.0
PRIMARY = "step +40, 600 steps"
SCENARIOS = {
    "attack-free": lambda t: 0.0,
    PRIMARY: lambda t: 40.0 if t >= ATTACK_AT else 0.0,
    "ramp to +40 over 200 steps": lambda t: 40.0 * min(1.0, max(0.0, (t - ATTACK_AT) / 200.0)),
    "step +40, 100 steps": lambda t: 40.0 if ATTACK_AT <= t < ATTACK_AT + 100 else 0.0,
    "pulses +40, 20 of every 100": lambda t: 40.0 if t >= ATTACK_AT and (t - ATTACK_AT) % 100 < 20 else 0.0,
    "small +8, 600 steps": lambda t: 8.0 if t >= ATTACK_AT else 0.0,
}


def observation(usage, rng):
    return {"usage": usage,
            "cpu": 0.8 * usage + rng.normal(0, 3.0),
            "latency": 200.0 + 2.0 * (usage - BASE) + rng.normal(0, 10.0),
            "error_rate": 2.0 + 0.02 * (usage - BASE) + rng.normal(0, 0.2)}


def run(system, scenario, seed, delay=None):
    rng = np.random.default_rng(seed)
    excess_fn = SCENARIOS[scenario]
    kw = {"adaptive": {}, "adaptive+window": {"evidence_window": 100, "require_fresh_evidence": True}}.get(system)
    g = Governor(store_path=tempfile.mkdtemp(), **(kw or {}))
    g.boundaries.create_boundary("key", "usage", LIMIT0)
    limit = LIMIT0
    damage = shed = tight = 0
    first_alarm_in_attack = None
    alarms_in_attack = 0
    human_acts_at = None
    first_tighten = None
    attack_on = scenario != "attack-free"
    for t in range(STEPS):
        usage = max(0.0, rng.normal(BASE, SD)) + excess_fn(t)
        det, _ = g.ingest_metrics("key", float(t), observation(usage, rng))
        if system in ("adaptive", "adaptive+window"):
            p = g.detect_and_propose_adaptation("key")
            if p:
                a, _ = g.authorize_proposal(p)
                g.apply_approved_proposal(a)
                tight += 1
                first_tighten = first_tighten if first_tighten is not None else t
            limit = g.boundaries.get_boundary("key").current_limit
        elif system == "oracle":
            limit = HUMAN_LIMIT if (attack_on and t >= ATTACK_AT) else LIMIT0
        elif system == "human":
            if attack_on and t >= ATTACK_AT and det["anomaly_detected"]:
                alarms_in_attack += 1
                if alarms_in_attack == 3 and human_acts_at is None:
                    human_acts_at = t + delay
            if human_acts_at is not None and t >= human_acts_at:
                limit = HUMAN_LIMIT
        admitted = min(usage, limit)
        damage += max(0.0, admitted - BASE) if (attack_on and t >= ATTACK_AT) else 0.0
        shed += usage - admitted
    ok, _ = g.verify_governance_integrity()
    return {"damage": damage, "shed_fraction": shed / max(1.0, sum([BASE] * STEPS)),
            "tightenings": tight, "first_tighten": first_tighten, "integrity_ok": bool(ok),
            "unsafe_loosenings": len(g.boundaries.unauthorized_loosenings()),
            "final_limit": limit}


def t_interval(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float(x.mean()), 0.0
    from math import sqrt
    df = len(x) - 1
    t = 1.96 + 2.4 / df + 3.0 / df ** 2  # t quantile, close for df >= 2
    return float(x.mean()), float(t * x.std(ddof=1) / sqrt(len(x)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--json")
    ap.add_argument("--limit0", type=float, default=100.0, help="starting limit (headroom sensitivity)")
    ap.add_argument("--only", nargs="*", help="restrict to these scenarios")
    a = ap.parse_args()
    global LIMIT0
    LIMIT0 = a.limit0
    systems = [("static", None), ("adaptive", None), ("adaptive+window", None),
               ("human, 30 steps", 30), ("human, 120 steps", 120), ("oracle", None)]
    out = {"per_cell": {}}
    print(f"{'scenario':30s} {'system':18s} {'damage (mean +/-95%)':>22s} {'shed usage':>11s} {'tightenings':>12s}")
    for sc in (a.only or SCENARIOS):
        for name, delay in systems:
            base = "human" if name.startswith("human") else name
            rs = [run(base if base != "static" else "static", sc, s, delay) for s in range(a.seeds)]
            dm, dci = t_interval([r["damage"] for r in rs])
            out["per_cell"][f"{sc} | {name}"] = {
                "damage_mean": dm, "damage_ci": dci,
                "shed_fraction": float(np.mean([r["shed_fraction"] for r in rs])),
                "tightenings": float(np.mean([r["tightenings"] for r in rs])),
                "unsafe_loosenings": int(sum(r["unsafe_loosenings"] for r in rs)),
                "integrity_ok": all(r["integrity_ok"] for r in rs),
                "first_tighten_median": (float(np.median([r["first_tighten"] for r in rs if r["first_tighten"] is not None]))
                                         if any(r["first_tighten"] is not None for r in rs) else None)}
            c = out["per_cell"][f"{sc} | {name}"]
            print(f"{sc:30s} {name:18s} {dm:12.0f} +/- {dci:6.0f} {c['shed_fraction']:10.3%} {c['tightenings']:12.2f}", flush=True)
    if PRIMARY not in (a.only or SCENARIOS) or "attack-free" not in (a.only or SCENARIOS):
        if a.json:
            json.dump(out, open(a.json, "w"), indent=2)
        return
    s_static = out["per_cell"][f"{PRIMARY} | static"]["damage_mean"]
    s_adapt = out["per_cell"][f"{PRIMARY} | adaptive"]["damage_mean"]
    out["summary"] = {
        "damage_reduction_vs_static": 1.0 - s_adapt / s_static if s_static else 0.0,
        "legit_blocked_clean": out["per_cell"]["attack-free | adaptive"]["shed_fraction"],
        "unsafe_loosenings": sum(v["unsafe_loosenings"] for v in out["per_cell"].values()),
        "all_integrity_ok": all(v["integrity_ok"] for v in out["per_cell"].values())}
    print(json.dumps(out["summary"], indent=2))
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
