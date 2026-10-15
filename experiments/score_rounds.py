#!/usr/bin/env python3
"""Score a multi-seed results file the way the plan's tables do, from the JSON alone.

    python experiments/score_rounds.py experiments/stack_trace_multiseed_results_round12a.json [--policies a,b]

Per roster, policy and agent: hung time saved (pooled over seeds, and the per-seed median), legitimate cost in
points (mean, median, max), seeds tightened, net time with burn and with rerun (ms per seed, and how many seeds are
negative), restore requests and operator decisions. Then per roster and policy: pooled should-not cost with and
without the slow-from-start agents, sustained saving, total net time, and where the operator decisions fall.
Added with round 12 after red team finding P5 (docs/RED_TEAM.md): the earlier rounds' tables came from scratch
scripts that were never committed.
"""
import argparse, json, statistics
from collections import defaultdict


def load(path):
    d = json.load(open(path))
    return [(r["roster"], r["seed"], r["outcomes"]) for r in d["per_seed"]]


def saved_pct(os_):
    bs = sum(o["bad_ns_static"] for o in os_); bg = sum(o["bad_ns_governed"] for o in os_)
    return None if bs == 0 else 100.0 * (1 - bg / bs)


def seed_saved(o):
    return None if o["bad_ns_static"] == 0 else 100.0 * (1 - o["bad_ns_governed"] / o["bad_ns_static"])


def fmt(v, w=6, d=1):
    return ("%" + str(w) + "s") % "-" if v is None else ("%" + str(w) + "." + str(d) + "f") % v


def score(runs, policies=None):
    by = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for roster, seed, out in runs:
        for pname, per in out.items():
            if policies and pname not in policies:
                continue
            for agent, o in per.items():
                if not agent.startswith("_"):
                    by[roster][pname][agent].append(o)
    for roster, pols in by.items():
        for pname, agents in pols.items():
            print(f"\n== {roster} / {pname}")
            print("%-20s %2s %6s %6s %6s %6s %6s %5s %10s %10s %5s %4s %4s" % (
                "agent", "sh", "saved", "s.med", "cost", "c.med", "c.max", "tight", "net_burn", "net_rerun", "neg", "rr", "dec"))
            tot = defaultdict(float)
            for agent, os_ in agents.items():
                n = len(os_)
                costs = [o["legit_cost_points"] for o in os_]
                sv = [v for v in (seed_saved(o) for o in os_) if v is not None]
                nb = [o.get("net_burn_ns", 0) / 1e6 for o in os_]; nr = [o.get("net_rerun_ns", 0) / 1e6 for o in os_]
                dec = sum(o.get("operator_decisions", 0) for o in os_); rr = sum(bool(o.get("restore_requested")) for o in os_)
                print("%-20s %2d %s %s %s %s %s %2d/%-2d %10.1f %10.1f %2d/%-2d %4d %4d" % (
                    agent, os_[0]["should_tighten"], fmt(saved_pct(os_)), fmt(statistics.median(sv) if sv else None),
                    fmt(statistics.mean(costs), 6, 2), fmt(statistics.median(costs), 6, 2), fmt(max(costs), 6, 1),
                    sum(o["tightened"] for o in os_), n, statistics.mean(nb), statistics.mean(nr),
                    sum(1 for v in nb if v < 0), n, rr, dec))
                tot["net_burn"] += sum(nb); tot["net_rerun"] += sum(nr); tot["static"] += sum(o.get("total_ns_static", 0) / 1e6 for o in os_)
                if not os_[0]["should_tighten"]:
                    tot["sn_cost"] += sum(costs); tot["sn_n"] += n; tot["sn_dec"] += dec
                    if os_[0]["profile"] != "slow_from_start":
                        tot["sn_cost_x"] += sum(costs); tot["sn_n_x"] += n
                    else:
                        tot["ss_dec"] += dec
                else:
                    tot["st_dec"] += dec
                    if agent == "attacker-inflate":
                        tot["inflate_dec"] += dec
                if os_[0]["profile"] == "runaway":
                    tot["sus_bs"] += sum(o["bad_ns_static"] for o in os_); tot["sus_bg"] += sum(o["bad_ns_governed"] for o in os_)
            sn = tot["sn_cost"] / tot["sn_n"] if tot["sn_n"] else None
            snx = tot["sn_cost_x"] / tot["sn_n_x"] if tot["sn_n_x"] else None
            sus = None if not tot["sus_bs"] else 100.0 * (1 - tot["sus_bg"] / tot["sus_bs"])
            pct = (100.0 * tot["net_burn"] / tot["static"]) if tot["static"] else 0.0
            print("   pooled should-not cost %s (without slow-from-start %s); sustained saved %s; net time per seed "
                  "burn %.0f ms / rerun %.0f ms (%.2f%% of static time); decisions: should-not %d (slow-from-start %d), "
                  "attackers %d (inflate %d)" % (fmt(sn, 5, 2), fmt(snx, 5, 2), fmt(sus, 5, 1),
                                                 tot["net_burn"] / max(1, len(next(iter(agents.values())))),
                                                 tot["net_rerun"] / max(1, len(next(iter(agents.values())))), pct,
                                                 tot["sn_dec"], tot["ss_dec"], tot["st_dec"], tot["inflate_dec"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+"); ap.add_argument("--policies", default=None)
    a = ap.parse_args()
    pols = set(a.policies.split(",")) if a.policies else None
    for p in a.paths:
        print(f"#### {p}")
        score(load(p), pols)


if __name__ == "__main__":
    main()
