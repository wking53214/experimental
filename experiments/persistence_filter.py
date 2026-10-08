"""
C: does requiring an alarm to persist cut the closed loop's false tightenings without losing events?

Filter m-of-n: a step is a violation only if at least m of the last n raw detector steps alarmed.
Chosen on development machines only, judged on held-out machines.
Selection rule (fixed before looking at held-out): among filters whose mean DEV event recall >= 0.9,
the lowest mean number of false-alarm episodes per machine on DEV (an episode = filtered alarms on
normal steps separated from the previous by at least 50 quiet steps; each episode can by itself supply
the three violations a tightening needs).

Needs raw alarms cached by experiments/persistence_filter.py --cache (runs the generative detector).
Run:  PYTHONPATH=. python -m experiments.persistence_filter [--cache] [--json out.json]
"""
import argparse
import json
import os

import numpy as np

DEV = ["machine-1-1", "machine-2-1", "machine-3-1", "machine-1-6"]
HELDOUT = ["machine-1-2", "machine-1-3", "machine-1-4", "machine-2-2",
           "machine-2-3", "machine-2-4", "machine-3-2", "machine-3-3"]
FILTERS = [(1, 1), (3, 5), (5, 10), (8, 10), (10, 20), (15, 30), (25, 40)]
CACHE = "experiments/cache_alarms"


def cache():
    from experiments.real_data_smd import load, stream
    os.makedirs(CACHE, exist_ok=True)
    for m in DEV + HELDOUT:
        tr, te, lb = load(m)
        np.savez(f"{CACHE}/{m}.npz", alarms=np.array(stream("Gc", tr, te), dtype=bool), labels=lb)
        print(m, "cached", flush=True)


def apply(alarms, m, n):
    a = alarms.astype(int)
    c = np.convolve(a, np.ones(n, dtype=int))[:len(a)]  # alarms in the last n steps (including this one)
    return c >= m


def segments(lb):
    d = np.diff(np.concatenate([[0], lb, [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def episodes(flags, normal, gap=50):
    idx = np.where(flags & normal)[0]
    if len(idx) == 0:
        return 0
    return 1 + int((np.diff(idx) >= gap).sum())


def evaluate(machine, m, n):
    z = np.load(f"{CACHE}/{machine}.npz")
    al, lb = z["alarms"], z["labels"].astype(int)
    f = apply(al, m, n)
    normal = lb == 0
    segs = segments(lb)
    return {"step_fpr": float(f[normal].mean()), "episodes": episodes(f, normal),
            "event_rec": float(np.mean([f[s:e].any() for s, e in segs])) if segs else float("nan"),
            "point_rec": float(f[~normal].mean()) if (~normal).any() else float("nan")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.cache:
        cache()
        return
    res = {f"{m}-of-{n}": {mach: evaluate(mach, m, n) for mach in DEV + HELDOUT} for m, n in FILTERS}

    def agg(name, machines):
        v = [res[name][x] for x in machines]
        return {k: float(np.nanmean([r[k] for r in v])) for k in ("step_fpr", "episodes", "event_rec", "point_rec")}

    summary = {name: {"dev": agg(name, DEV), "heldout": agg(name, HELDOUT)} for name in res}
    eligible = [n for n in summary if summary[n]["dev"]["event_rec"] >= 0.9]
    selected = min(eligible, key=lambda n: summary[n]["dev"]["episodes"]) if eligible else None
    print(f"{'filter':10s} | DEV: fpr   episodes event  point | HELDOUT: fpr   episodes event  point")
    for n, v in summary.items():
        d, h = v["dev"], v["heldout"]
        print(f"{n + (' *' if n == selected else ''):10s} |      {d['step_fpr']:.3f}  {d['episodes']:6.1f}   {d['event_rec']:.2f}   {d['point_rec']:.2f} |          {h['step_fpr']:.3f}  {h['episodes']:6.1f}   {h['event_rec']:.2f}   {h['point_rec']:.2f}")
    if a.json:
        json.dump({"per_machine": res, "summary": summary, "selected_on_dev": selected}, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
