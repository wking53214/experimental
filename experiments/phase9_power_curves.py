"""
Detection probability versus attack strength, per attack family.

One metric (latency) attacked for 20 steps from the start of a 40-step episode.
20 seeds per point. Compares the hybrid with and without the temporal layer.

Run: python -m experiments.phase9_power_curves [--json out.json]
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from experiments.phase9_end_to_end import run_episode, train
from src.governance.phase9c_evolutionary import AttackChromosome

SEEDS = range(20)
# strength -> magnitude parameter understood by apply_attack
FAMILIES = {
    "shift (sigma)": ("shift", [0.25, 0.5, 1.0, 1.5, 2.0], lambda s: 1.0 + s),
    "variance (spread ratio)": ("variance", [1.25, 1.5, 2.0, 3.0, 4.0], lambda s: s),
    "oscillate (amplitude, sigma)": ("oscillate", [0.5, 1.0, 1.5, 2.0, 3.0], lambda s: 1.0 + s),
    "decorrelate (fraction replaced)": ("decorrelate", [0.2, 0.4, 0.6, 0.8, 1.0], lambda s: 1.0 + 3.0 * s),
}


def rate(kind_detector, attack_kind, mag):
    hits = 0
    for s in SEEDS:
        trained = train(kind_detector, kind_detector, s)
        rng = np.random.default_rng(6000 + s)
        ch = AttackChromosome(["latency"], [mag], [20], [0], kinds=[attack_kind])
        hits += run_episode(trained, ch, rng)[0]
    return hits / len(SEEDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    out = {}
    for name, (kind, strengths, to_mag) in FAMILIES.items():
        out[name] = {"strengths": strengths, "hybrid": [], "hybrid+temporal": []}
        for st in strengths:
            out[name]["hybrid"].append(rate("H", kind, to_mag(st)))
            out[name]["hybrid+temporal"].append(rate("HT", kind, to_mag(st)))
        print(f"{name}")
        print("   strength        " + "  ".join(f"{s:>5}" for s in strengths))
        print("   hybrid          " + "  ".join(f"{r:5.2f}" for r in out[name]["hybrid"]))
        print("   +temporal layer " + "  ".join(f"{r:5.2f}" for r in out[name]["hybrid+temporal"]), flush=True)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
