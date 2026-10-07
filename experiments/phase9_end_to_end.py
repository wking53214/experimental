"""
Phase 9 end-to-end experiment.

E1: evolve attacks (9C) against several detector configurations built from 9A/9B.
E2: legitimate gradual drift: false positives, 9D drift detection and baseline gating.
E3: staged attacks: does 9E raise a warning before the violation step?

Synthetic system: 5 correlated metrics, ~10% coefficient of variation.
Run:  python -m experiments.phase9_end_to_end [--seeds 3] [--json out.json]
"""
from __future__ import annotations

import argparse
import copy
import json
import time

import numpy as np

from src.governance.metrics import DetectorPipeline
from src.governance.multivariate import GenerativeAnomalyDetector, calibrated_mahalanobis_threshold
from src.governance.phase9_integration import HybridDetectorPipeline
from src.governance.phase9c_evolutionary import EvolutionaryAdversary
from src.governance.phase9d_concept_drift import AdaptiveDetector
from src.governance.phase9e_precursors import EarlyWarningSystem

METRICS = ["latency", "error_rate", "throughput", "cpu", "queue_depth"]
MEANS = np.array([200.0, 2.0, 1000.0, 50.0, 30.0])
CV = 0.10
CORR = np.array([
    [1.0, 0.3, -0.5, 0.6, 0.8],
    [0.3, 1.0, -0.3, 0.2, 0.3],
    [-0.5, -0.3, 1.0, -0.2, -0.4],
    [0.6, 0.2, -0.2, 1.0, 0.5],
    [0.8, 0.3, -0.4, 0.5, 1.0],
])
SD = MEANS * CV
COV = CORR * np.outer(MEANS * CV, MEANS * CV)
BASELINE_STEPS = 120
EPISODE_STEPS = 40
DAMAGE_NORM = 20.0
SHIFT_SIGMA = 1.0  # a magnitude m shifts a metric by (m-1)*SHIFT_SIGMA standard deviations
CALIBRATED_MD = calibrated_mahalanobis_threshold(len(METRICS))


def sample(rng, scale=1.0):
    v = rng.multivariate_normal(MEANS * scale, COV * scale**2)
    return {m: float(x) for m, x in zip(METRICS, v)}


class Variant:
    """Uniform interface: ingest(ts, obs) -> bool (alarm at this step)."""

    def __init__(self, name, kind, lam=0.2):
        self.name, self.kind = name, kind
        if kind == "T":
            self.d = DetectorPipeline("b", 100)
        elif kind in ("G", "Gc"):
            self.d = GenerativeAnomalyDetector("b", 20, 3.0 if kind == "G" else None)
        elif kind == "H":
            self.d = HybridDetectorPipeline("b", 100)
        elif kind == "HT":
            self.d = HybridDetectorPipeline("b", 100, temporal_shift=True, temporal_smoothing=lam)
        elif kind == "H3":
            self.d = HybridDetectorPipeline("b", 100, mahalanobis_threshold=3.0)

    def ingest(self, ts, obs):
        k = self.kind
        if k in ("G", "Gc"):
            r = self.d.detect_anomaly(obs)
            self.d.ingest_observation(obs)
            return bool(r["anomaly_detected"])
        self.d.ingest_metrics(ts, obs)
        return bool(self.d.detect_anomalies()["anomaly_detected"])

    def md_score(self, obs):
        gd = self.d if self.kind in ("G", "Gc") else self.d.generative_detector
        return gd.detect_anomaly(obs)


VARIANTS = [("traditional", "T"), ("generative(MD>3)", "G"),
            ("generative(default)", "Gc"), ("hybrid(MD>3)", "H3"), ("hybrid(default)", "H"),
            ("hybrid+temporal", "HT")]


def train(kind, name, seed, lam=0.2):
    rng = np.random.default_rng(10_000 + seed)
    v = Variant(name, kind, lam)
    for i in range(BASELINE_STEPS):
        v.ingest(1000.0 + i, sample(rng))
    return v


def apply_attack(obs, metric, kind, mag, step_in_window, rng):
    """Apply one attack family to one metric of an observation, in place.

    shift: move the mean. variance: scale the spread around the mean (mean preserved).
    oscillate: alternate the sign of a shift every step (average shift ~0).
    decorrelate: blend in independent noise, so the metric keeps its own mean and
    variance but loses its correlation with the other metrics.
    """
    j = METRICS.index(metric)
    mu, sd = MEANS[j], SD[j]
    if kind == "shift":
        obs[metric] += (mag - 1.0) * SHIFT_SIGMA * sd
    elif kind == "variance":
        obs[metric] = mu + (obs[metric] - mu) * max(mag, 0.05)
    elif kind == "oscillate":
        sign = 1.0 if step_in_window % 2 == 0 else -1.0
        obs[metric] += sign * (mag - 1.0) * SHIFT_SIGMA * sd
    elif kind == "decorrelate":
        w = min(1.0, max(0.0, (mag - 1.0) / 3.0))
        z = (obs[metric] - mu) / sd
        obs[metric] = mu + sd * (np.sqrt(1.0 - w * w) * z + w * rng.standard_normal())
    else:
        raise ValueError(kind)


def run_episode(trained, chrom, rng, attack=True):
    """Returns (detected_during_attack, any_flag, steps_flagged, steps_attacked)."""
    v = copy.deepcopy(trained)
    windows = {}
    if attack:
        kinds = chrom.kinds if getattr(chrom, "kinds", None) else ["shift"] * len(chrom.metric_targets)
        for t, mag, dur, dly, kind in zip(chrom.metric_targets, chrom.magnitudes,
                                          chrom.durations, chrom.delays, kinds):
            s = int(dly)
            windows[t] = (s, min(EPISODE_STEPS, s + max(1, int(dur))), mag, kind)
    detected = False
    any_flag = 0
    attacked = 0
    for step in range(EPISODE_STEPS):
        obs = sample(rng)
        active = False
        for t, (s, e, mag, kind) in windows.items():
            if s <= step < e:
                apply_attack(obs, t, kind, mag, step - s, rng)
                active = True
        flag = v.ingest(2000.0 + step, obs)
        any_flag += flag
        attacked += active
        if flag and active:
            detected = True
    return detected, any_flag, attacked


def damage_of(chrom):
    d = sum(abs(m - 1.0) * min(max(1, int(du)), EPISODE_STEPS)
            for m, du in zip(chrom.magnitudes, chrom.durations))
    return float(min(1.0, d / DAMAGE_NORM))


def clean_fpr(trained, rng, episodes=10):
    flags = steps = ep_flag = 0
    for _ in range(episodes):
        _, f, _ = run_episode(trained, None, rng, attack=False)
        flags += f
        steps += EPISODE_STEPS
        ep_flag += f > 0
    return flags / steps, ep_flag / episodes


def e1(seeds):
    out = {}
    for name, kind in VARIANTS:
        rows = []
        for seed in range(seeds):
            np.random.seed(seed)
            trained = train(kind, name, seed)
            rng = np.random.default_rng(500 + seed)
            step_fpr, ep_fpr = clean_fpr(trained, np.random.default_rng(900 + seed))
            adv = EvolutionaryAdversary(population_size=20, generations=10)
            per_gen = []
            calls = []
            evaded = []

            def evaluate(chrom):
                det, _, _ = run_episode(trained, chrom, rng)
                dmg = damage_of(chrom)
                calls.append((det, dmg))
                if not det:
                    evaded.append((dmg, chrom))
                return dmg, det

            best = adv.run_evolution(METRICS, evaluate)
            for g in range(adv.generations):
                chunk = calls[g * 20:(g + 1) * 20]
                per_gen.append(sum(d for d, _ in chunk) / len(chunk))
            best_evaded = max(evaded, key=lambda x: x[0]) if evaded else None
            rows.append({
                "step_fpr": step_fpr, "episode_fpr": ep_fpr,
                "det_rate_gen0": per_gen[0], "det_rate_last": per_gen[-1],
                "det_rate_all": sum(d for d, _ in calls) / len(calls),
                "evasive_attacks": len(evaded),
                "best_undetected_damage": best_evaded[0] if best_evaded else 0.0,
                "best_fitness": best.fitness,
            })
        out[name] = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
    return out


def e2(seed=0, steps=200):
    out = {}
    for name, kind in VARIANTS:
        trained = train(kind, name, seed)
        v = copy.deepcopy(trained)
        rng = np.random.default_rng(77 + seed)
        flags = 0
        for i in range(steps):
            flags += v.ingest(3000.0 + i, sample(rng, 1.0 + 0.2 * i / steps))
        out[name] = {"step_fpr_under_drift": flags / steps}

    # 9D on the generative model's own distances
    trained = train("G", "g", seed)
    v = copy.deepcopy(trained)
    ad = AdaptiveDetector("b")
    rng = np.random.default_rng(77 + seed)
    for i in range(steps):
        obs = sample(rng, 1.0 + 0.2 * i / steps)
        r = v.md_score(obs)
        ad.process_observation(obs, r["mahalanobis_distance"])
        v.d.ingest_observation(obs)
    s = ad.get_adaptation_summary()
    out["9D"] = {"acceptance_rate": s["acceptance_rate"],
                 "has_drift": bool(s["current_drift"]["has_drift"]),
                 "drift_type": s["current_drift"]["drift_type"],
                 "drift_score": float(s["current_drift"]["drift_score"])}
    return out


def staged_episode(rng, violate=True):
    obs_list, viol = [], []
    for step in range(40):
        o = sample(rng)
        v = False
        if violate and 25 <= step < 28:
            o["queue_depth"] *= 1.3
        if violate and step == 28:
            o["error_rate"] *= 4.0
            v = True
        obs_list.append(o)
        viol.append(v)
    return obs_list, viol


def e3(seed=0, train_eps=8, test_eps=8):
    rng = np.random.default_rng(300 + seed)
    gen = GenerativeAnomalyDetector("b", 20)
    for _ in range(BASELINE_STEPS):
        gen.ingest_observation(sample(rng))
    ews = EarlyWarningSystem("b")
    for _ in range(train_eps):
        obs_l, viol = staged_episode(rng)
        for o, v in zip(obs_l, viol):
            s = gen.detect_anomaly(o)["anomaly_score"]
            ews.process_observation(o, s, is_violation=v)
        ews.update_from_violation()
    patterns = len(ews.precursor_learner.learned_patterns)
    pattern_sigs = sorted(ews.precursor_learner.learned_patterns)
    filtered = copy.deepcopy(ews)
    filtered.precursor_learner.learned_patterns.pop("normal", None)

    test = [staged_episode(rng) for _ in range(test_eps)]
    out = {"patterns_learned": patterns, "pattern_signatures": pattern_sigs}
    for label, system in (("as_shipped", ews), ("without_normal_signature", filtered)):
        warned_before = false_alarms = quiet_steps = 0
        leads = []
        for obs_l, viol in test:
            s = copy.deepcopy(system)
            recs = []
            for o, v in zip(obs_l, viol):
                sc = gen.detect_anomaly(o)["anomaly_score"]
                recs.append(s.process_observation(o, sc, is_violation=v))
            vi = viol.index(True)
            pre = [i for i in range(vi - 5, vi) if recs[i]["warning_level"] != "normal"]
            warned_before += bool(pre)
            if pre:
                leads.append(vi - pre[0])
            for i, r in enumerate(recs):
                if i < 20:
                    quiet_steps += 1
                    false_alarms += r["warning_level"] != "normal"
        out[label] = {"violations_warned_before": warned_before, "test_episodes": test_eps,
                      "mean_lead_steps": float(np.mean(leads)) if leads else None,
                      "false_alarm_rate_quiet_steps": false_alarms / quiet_steps}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--json")
    a = ap.parse_args()
    t0 = time.time()
    res = {"calibrated_md_threshold": CALIBRATED_MD, "e1": e1(a.seeds), "e2": e2(), "e3": e3()}
    res["runtime_s"] = time.time() - t0
    print(json.dumps(res, indent=2))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
