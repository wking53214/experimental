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


def c7():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "tests/test_durable_state.py"],
                       capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return ("PASS" if r.returncode == 0 else "FAIL"), last


def c8():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "tests/test_signal_interface.py",
                        "tests/test_adjudication_server_hardening.py", "-k", "Signal or signal or never or example"],
                       capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return ("PASS" if r.returncode == 0 else "FAIL"), last


def c9():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-rs", "tests/test_integration_dgk.py",
                        "tests/test_integration_stack_traps.py"], capture_output=True, text=True)
    out = r.stdout.strip().splitlines()
    skipped = any("SKIPPED" in l for l in out)
    last = out[-1] if out else ""
    return ("PASS" if r.returncode == 0 and not skipped else ("NOT MEASURED" if skipped else "FAIL")), last + (
        " (DGK not installed: its tests were skipped)" if skipped else "")


def c10():
    """Bounded autonomy with validated tightening (docs/SUCCESS_CRITERIA.md, criterion 10)."""
    # The bounded validator (round 5, second run, seeds 4001-4020) is the current code; the first run's file is the
    # fallback so the criterion can still be scored from either.
    name = "stack_trace_multiseed_results_validated_bounded.json"
    d = load(name)
    if d is None:
        name = "stack_trace_multiseed_results_validated.json"; d = load(name)
    if d is None:
        return "NOT MEASURED", "run experiments/stack_trace_multiseed.py --seeds 20 --seed-base 4000 --policies validated --no-sweep --tag _validated_bounded"
    pol = "validated"
    runs = [(r["roster"], r["seed"], r["outcomes"][pol]) for r in d["per_seed"] if pol in r["outcomes"]]
    if not runs:
        return "NOT MEASURED", "no 'validated' policy in the results"
    should = [o for _r, _s, out in runs for a, o in out.items() if not a.startswith("_")
              and o["profile"] in ("runaway", "runaway_episode") or (not a.startswith("_") and o["profile"].startswith("attacker"))]
    a_ok = sum(o["tightened"] for o in should); a_n = len(should)
    bad_s = sum(o["bad_ns_static"] for o in should); bad_g = sum(o["bad_ns_governed"] for o in should)
    b_val = 1 - bad_g / bad_s if bad_s else 0.0
    c_ok = all(out["_audit_intact"] for _r, _s, out in runs)
    wrong = [o for _r, _s, out in runs for a, o in out.items() if not a.startswith("_") and not o["should_tighten"] and o["tightened"]]
    def degraded_within(o, k=6):
        t = o["first"]
        return any(v["verdict"] == "DEGRADED" and t is not None and v["slice"] - t <= k for v in o.get("verdicts", [])) and o.get("restore_requested")
    d_wrong = sum(degraded_within(o) for o in wrong)
    runaways = [o for _r, _s, out in runs for a, o in out.items() if not a.startswith("_") and o["profile"] in ("runaway", "runaway_episode")]
    d_run = sum(any(v["verdict"] == "DEGRADED" and v["bad_in_window"] > 0 for v in o.get("verdicts", [])) for o in runaways)
    healthy = [o for _r, _s, out in runs for a, o in out.items() if not a.startswith("_") and o["profile"] == "healthy"
               and o["legit_fail_rate_static"] <= 0.2]
    e_bad = sum(o["tightened"] or o.get("restore_requested", False) for o in healthy)
    parts = [a_ok >= 0.95 * a_n, b_val >= 0.10, c_ok, (d_wrong >= 0.8 * len(wrong) if wrong else True) and d_run <= 0.10 * len(runaways), e_bad == 0]
    detail = (f"(a) should-tighten agent-seeds tightened {a_ok}/{a_n}; (b) hung time saved {b_val:.0%} (need 10%); (c) integrity {'ok' if c_ok else 'BROKEN'}; "
              f"(d) wrong tightenings DEGRADED+restore within 6 slices {d_wrong}/{len(wrong)}, runaways DEGRADED while hanging {d_run}/{len(runaways)}; "
              f"(e) healthy (tail <= 0.2%) tightened or restore-requested {e_bad}/{len(healthy)}")
    return ("PASS" if all(parts) else "FAIL"), detail + " | " + "".join("P" if x else "F" for x in parts) + f" [{name}]"


if __name__ == "__main__":
    rows = [("1 safety", c1), ("2 detection vs baseline", c2), ("3 no crying wolf", c3),
            ("4 responds to real trouble", c4), ("5 value over alternatives", c5), ("6 poisoning survivable", c6),
            ("7 restart never loosens", c7),
            ("8 signals never loosen", c8),
            ("9 stack integrations work", c9),
            ("10 bounded autonomy validated", c10)]
    for name, fn in rows:
        status, detail = fn()
        print(f"{status:13s} {name:28s} {detail}")
