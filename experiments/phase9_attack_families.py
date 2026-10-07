"""
Does the hardening generalize beyond mean shifts?

For each attack family, evolve a fresh adversary (5 seeds) against the hybrid detector
with and without the temporal layer, and report detection and evasion.

Run: python -m experiments.phase9_attack_families [--json out.json]
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from experiments.phase9_end_to_end import (
    METRICS, EvolutionaryAdversary, clean_fpr, damage_of, run_episode, train,
)
from src.governance.phase9c_evolutionary import ATTACK_KINDS

SEEDS = range(5)
FAMILIES = [(k,) for k in ATTACK_KINDS] + [ATTACK_KINDS]


def evolve(kind, seed, attack_kinds):
    np.random.seed(seed)
    trained = train(kind, kind, seed)
    rng = np.random.default_rng(500 + seed)
    step_fpr, _ = clean_fpr(trained, np.random.default_rng(900 + seed))
    calls, evasive = [], []

    def evaluate(ch):
        det, _, _ = run_episode(trained, ch, rng)
        calls.append(det)
        if not det:
            evasive.append(ch)
        return damage_of(ch), det

    EvolutionaryAdversary(population_size=20, generations=10, attack_kinds=attack_kinds)\
        .run_evolution(METRICS, evaluate)
    return {"step_fpr": step_fpr, "det_gen0": float(np.mean(calls[:20])),
            "det_last": float(np.mean(calls[-20:])), "det_all": float(np.mean(calls)),
            "evasive": len(evasive),
            "best_undetected_damage": max((damage_of(c) for c in evasive), default=0.0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    res = {}
    for fam in FAMILIES:
        name = fam[0] if len(fam) == 1 else "mixed"
        res[name] = {}
        for label, kind in (("hybrid", "H"), ("hybrid+temporal", "HT")):
            rows = [evolve(kind, s, fam) for s in SEEDS]
            res[name][label] = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
        h, t = res[name]["hybrid"], res[name]["hybrid+temporal"]
        print(f"{name:12s} det_all {h['det_all']:.2f} -> {t['det_all']:.2f} | det_last {h['det_last']:.2f} -> {t['det_last']:.2f} | "
              f"best undetected {h['best_undetected_damage']:.2f} -> {t['best_undetected_damage']:.2f} | fpr {h['step_fpr']:.3f} / {t['step_fpr']:.3f}", flush=True)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
