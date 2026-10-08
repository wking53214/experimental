"""
Evaluate docs/SUCCESS_CRITERIA.md from the result files. Prints PASS / FAIL / NOT MEASURED per criterion.
Run:  PYTHONPATH=. python -m experiments.scorecard
"""
import json
import os

import numpy as np

R = "experiments"


def load(name):
    p = os.path.join(R, name)
    return json.load(open(p)) if os.path.exists(p) else None


def c1():
    soak = load("phase13_soak_results.json")
    if soak is None:
        return "NOT MEASURED", "no soak results"
    bad = [(m, c) for m, cs in soak.items() for c, r in cs.items() if not r["integrity_ok"]]
    return ("PASS" if not bad else "FAIL"), f"integrity_ok in {sum(len(c) for c in soak.values()) - len(bad)}/{sum(len(c) for c in soak.values())} soak runs (test suite status is checked by CI)"


def c2():
    d = load("criteria_detection_results.json")
    if d is None:
        return "NOT MEASURED", "run experiments/criteria_detection.py"
    wins = sum(v["detector_recall"] >= v["baseline_recall"] for v in d.values())
    return ("PASS" if wins >= 6 else "FAIL"), f"detector recall >= baseline at matched FPR on {wins}/{len(d)} held-out machines"


def c3(key="default"):
    soak = load("phase13_soak_results.json")
    if soak is None:
        return "NOT MEASURED", "no soak results"
    ok = sum(cs[key]["unjustified_tightenings"] <= 1 for cs in soak.values())
    return ("PASS" if ok >= 10 else "FAIL"), f"{ok}/{len(soak)} machines with <= 1 unjustified tightening ({key})"


def c4(key="default"):
    soak = load("phase13_soak_results.json")
    if soak is None or "justified_response" not in next(iter(soak.values()))[key]:
        return "NOT MEASURED", "soak results do not record justified responses yet"
    elig = [cs[key] for cs in soak.values() if cs[key]["anomaly_events"] >= 3]
    ok = sum(r["justified_response"] for r in elig)
    return ("PASS" if ok >= 8 else "FAIL"), f"{ok}/{len(elig)} eligible machines tightened within 100 steps of an anomaly onset"


def c5():
    d = load("value_vs_alternatives_results.json")
    if d is None:
        return "NOT MEASURED", "run experiments/value_vs_alternatives.py"
    s = d["summary"]
    ok = s["damage_reduction_vs_static"] >= 0.40 and s["legit_blocked_clean"] <= 0.01 and s["unsafe_loosenings"] == 0
    return ("PASS" if ok else "FAIL"), (f"damage reduction vs static {s['damage_reduction_vs_static']:.0%} (need 40%), "
                                       f"legit blocked in clean runs {s['legit_blocked_clean']:.2%} (max 1%), unsafe loosenings {s['unsafe_loosenings']}")


def c6():
    d = load("baseline_poisoning_results.json")
    if d is None:
        return "NOT MEASURED", "no poisoning results"
    v = d["contaminate 10% | calibrated + trim 25%"]["det_3sd"]
    return ("PASS" if v >= 0.80 else "FAIL"), f"3 sd detection with 10% contamination and trimmed fit: {v:.0%} (need 80%)"


if __name__ == "__main__":
    rows = [("1 safety", c1), ("2 detection vs baseline", c2), ("3 no crying wolf", c3),
            ("4 responds to real trouble", c4), ("5 value over alternatives", c5), ("6 poisoning survivable", c6)]
    for name, fn in rows:
        status, detail = fn()
        print(f"{status:13s} {name:28s} {detail}")
