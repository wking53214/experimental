"""
Adversarial feedback loop: evolved attacks tune the temporal layer, then a fresh
adversary attacks the hardened detector. Compares against the unhardened hybrid.

Run: python -m experiments.phase9_adversarial_training [--json out.json]
"""
from __future__ import annotations

import argparse
import copy
import json

import numpy as np

from experiments.phase9_end_to_end import (
    EPISODE_STEPS, METRICS, SHIFT_SIGMA, EvolutionaryAdversary, clean_fpr, damage_of, run_episode, train,
)

LAMBDAS = [0.05, 0.1, 0.2, 0.3, 0.5]
FPR_BUDGET = 0.045  # step false-alarm rate allowed (the unhardened hybrid sits near 3.8%)
TRAIN_SEEDS = (0, 1)


def evolve(kind, lam, seed):
    """Evolve attacks against a freshly trained detector; return stats and evasive attacks."""
    np.random.seed(seed)
    trained = train(kind, kind, seed, lam)
    rng = np.random.default_rng(500 + seed)
    step_fpr, _ = clean_fpr(trained, np.random.default_rng(900 + seed))
    calls, evasive = [], []

    def evaluate(ch):
        det, _, _ = run_episode(trained, ch, rng)
        calls.append(det)
        if not det:
            evasive.append(copy.deepcopy(ch))
        return damage_of(ch), det

    adv = EvolutionaryAdversary(population_size=20, generations=10)
    adv.run_evolution(METRICS, evaluate)
    best_dmg = max((damage_of(c) for c in evasive), default=0.0)
    return {"step_fpr": step_fpr, "det_gen0": float(np.mean(calls[:20])),
            "det_last": float(np.mean(calls[-20:])), "det_all": float(np.mean(calls)),
            "evasive": len(evasive), "best_undetected_damage": best_dmg}, evasive


def profile(attacks):
    if not attacks:
        return {"n": 0}
    shifts = [max(abs(m - 1.0) * SHIFT_SIGMA for m in c.magnitudes) for c in attacks]
    durs = [max(int(d) for d in c.durations) for c in attacks]
    return {"n": len(attacks), "mean_max_shift_sigma": float(np.mean(shifts)),
            "mean_duration_steps": float(np.mean(durs)),
            "frac_shift_below_1_sigma": float(np.mean([s < 1.0 for s in shifts])),
            "frac_duration_3_or_less": float(np.mean([d <= 3 for d in durs]))}


def mean_stats(rows):
    return {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}


def score_lambdas(attack_set):
    """Feed attacks back: detection of the attack set and clean FPR per smoothing value."""
    out = {}
    for lam in LAMBDAS:
        hits = n = 0
        fprs = []
        for s in TRAIN_SEEDS:
            trained = train("HT", "HT", s, lam)
            rng = np.random.default_rng(7000 + s)
            fprs.append(clean_fpr(trained, np.random.default_rng(8000 + s))[0])
            for ch in attack_set:
                hits += run_episode(trained, ch, rng)[0]
                n += 1
        out[lam] = {"detect_rate": hits / max(1, n), "step_fpr": float(np.mean(fprs))}
    return out


def pick(scores):
    ok = {l: s for l, s in scores.items() if s["step_fpr"] <= FPR_BUDGET}
    pool = ok or scores
    return max(pool, key=lambda l: pool[l]["detect_rate"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()
    res = {"rounds": [], "lambda_scores": []}

    # Round 0: attacks that got through the unhardened hybrid
    attack_set = []
    for s in TRAIN_SEEDS:
        attack_set += evolve("H", None, 20 + s)[1]
    print(f"round 0: {len(attack_set)} attacks evaded the unhardened hybrid", flush=True)

    for r in range(1, a.rounds + 1):
        scores = score_lambdas(attack_set)
        lam = pick(scores)
        res["lambda_scores"].append({"round": r, "scores": scores, "chosen": lam})
        print(f"round {r}: chose smoothing {lam}; "
              + ", ".join(f"{l}:{s['detect_rate']:.2f}/{s['step_fpr']:.3f}" for l, s in scores.items()), flush=True)
        held = [100 + 10 * r + i for i in range(5)]  # fresh seeds every round
        base_rows = [evolve("H", None, s)[0] for s in held]
        rows, new_evasive = [], []
        for s in held:
            st, ev = evolve("HT", lam, s)
            rows.append(st)
            new_evasive += ev
        entry = {"round": r, "smoothing": lam, "baseline": mean_stats(base_rows),
                 "hardened": mean_stats(rows), "remaining_evasive_profile": profile(new_evasive)}
        res["rounds"].append(entry)
        print("   baseline :", json.dumps({k: round(v, 3) for k, v in entry["baseline"].items()}), flush=True)
        print("   hardened :", json.dumps({k: round(v, 3) for k, v in entry["hardened"].items()}), flush=True)
        print("   still-evasive profile:", json.dumps({k: round(v, 3) for k, v in entry["remaining_evasive_profile"].items()}), flush=True)
        attack_set += new_evasive[:120]
    print(json.dumps(res, indent=2))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
