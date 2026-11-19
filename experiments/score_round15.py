#!/usr/bin/env python3
"""Score the round 15 predictions (docs/TRACE_TEST_PLAN.md) from experiments/stack_trace_round15{a,b}.json."""
import json, sys
from pathlib import Path
import numpy as np

D = Path(__file__).resolve().parent
A = json.loads((D / "stack_trace_round15a.json").read_text())["results"]["calm_v1"]
B = json.loads((D / "stack_trace_round15b.json").read_text())["results"]
REC, SLC, TWO = "recommended", "recommended_slice", "twoway_h16"
CALM = ["calm-00", "calm-25", "calm-50", "calm-75", "calm-88", "calm-94", "calm-97"]
LEN = {32: "8000", 64: "16000", 128: "32000"}
ok = lambda b: "Yes" if b else "No"


def a(n, pol, ag, key):
    return A[LEN[n]][pol][ag][key]


def sv(n, pol, ag):
    return 100 * a(n, pol, ag, "saved_share")


def table_a():
    print("## 15a, saved share (%) / seeds tightened of 20, by calm share and run length")
    for pol in (REC, SLC, TWO):
        print(f"\n{pol}")
        print("agent        " + "  ".join(f"{n:>3} slices" for n in LEN))
        for ag in CALM:
            print(f"{ag:<12} " + "  ".join(f"{sv(n,pol,ag):5.1f}/{a(n,pol,ag,'tightened_seeds'):>2}  " for n in LEN))
    print("\nhonest agents: cost points / seeds tightened")
    for pol in (REC, SLC, TWO):
        for ag in ("honest-healthy", "honest-tail", "honest-regressed"):
            print(f"{pol:<18} {ag:<17} " + "  ".join(f"{a(n,pol,ag,'legit_cost_points_mean'):5.2f}/{a(n,pol,ag,'tightened_seeds'):>2}  " for n in LEN))
    print("\noperator decisions summed over the roster (all seeds)")
    for pol in (REC, SLC, TWO):
        print(f"{pol:<18} " + "  ".join(f"{n}: {sum(a(n,pol,ag,'operator_decisions_total') for ag in A[LEN[n]][pol])}" for n in LEN))
    print("\n## 15a predictions")
    s32, s128 = sv(32, SLC, "calm-00"), sv(128, SLC, "calm-00")
    print(f"A1 steady runaway {s32:.1f}% -> {s128:.1f}%: {ok(abs(s128-s32)<=3 and s128>=24)}")
    pul = CALM[1:]
    t_ok = all(a(n, SLC, ag, "tightened_seeds") >= 18 for n in LEN for ag in pul)
    rise = all(sv(128, SLC, ag) >= sv(32, SLC, ag) for ag in pul)
    twenty = all(sv(128, SLC, ag) >= 20 for ag in CALM[1:5])
    print(f"A2 tightened>=18 everywhere {t_ok}; rises with length {rise}; >=20% to calm 87.5% {twenty}: {ok(t_ok and rise and twenty)}")
    gaps = {ag: sv(128, SLC, ag) - sv(128, TWO, ag) for ag in pul}
    print("A3 gaps at 128 slices (slice minus twoway_h16):", {k: round(v, 1) for k, v in gaps.items()})
    print(f"   <=5 up to period 16: {all(gaps[ag] <= 5 for ag in CALM[1:6])}; >=6 at period 32: {gaps['calm-97'] >= 6}: "
          f"{ok(all(gaps[ag] <= 5 for ag in CALM[1:6]) and gaps['calm-97'] >= 6)}")
    c64, c128 = a(64, SLC, "honest-tail", "legit_cost_points_mean"), a(128, SLC, "honest-tail", "legit_cost_points_mean")
    t64, t128 = a(64, SLC, "honest-tail", "tightened_seeds"), a(128, SLC, "honest-tail", "tightened_seeds")
    r128 = a(128, REC, "honest-tail", "tightened_seeds")
    print(f"A4 slice: tightened {t64},{t128}; cost {c64:.2f}->{c128:.2f}; recommended tightened at 128: {r128}: "
          f"{ok(t64 >= 19 and t128 >= 19 and c128 >= 1.2 and abs(c128-c64) <= 0.4 and r128 >= 14)}")
    d = a(128, SLC, "honest-regressed", "legit_cost_points_mean") - a(32, SLC, "honest-regressed", "legit_cost_points_mean")
    print(f"A5 honest-regressed cost change 32->128: {d:+.2f}: {ok(d <= 1.5)}")
    dec = lambda n: sum(a(n, SLC, ag, "operator_decisions_total") for ag in A[LEN[n]][SLC])
    print(f"A6 decisions {dec(32)} -> {dec(128)} (ratio {dec(128)/max(1,dec(32)):.2f}): {ok(dec(128) <= 2.5 * dec(32))}")


HON = ["healthy", "healthy-tail", "regressed", "drifting", "slowstart", "batchjob"]
MIS = ["runaway-steady", "runaway-mild", "runaway-episode", "lowslow", "inflate", "pulse-8"]


def pool(roster, pol, key="net_burn_ns_mean"):
    r = B[roster]["8000"][pol]
    H = np.mean([r[x][key] for x in HON]); M = np.mean([r[x][key] for x in MIS])
    TH = np.mean([r[x]["total_ns_static_mean"] for x in HON]); TM = np.mean([r[x]["total_ns_static_mean"] for x in MIS])
    return H, M, TH, TM


def share_curve(roster, pol, key="net_burn_ns_mean", m=0.0):
    H, M, TH, TM = pool(roster, pol, key)
    return 100 * (m * M + (1 - m) * H) / (m * TM + (1 - m) * TH)


def breakeven(roster, pol, key="net_burn_ns_mean"):
    H, M, _, _ = pool(roster, pol, key)
    if H >= 0 and M >= 0: return 0.0
    if M <= 0: return None
    return -H / (M - H)


def table_b():
    print("\n## 15b, pools (ms per agent-run, net burn) and breakeven share")
    for roster, lab in (("book_v1", "100 ms"), ("book75_v1", "75 ms"), ("book60_v1", "60 ms")):
        for pol in (REC, SLC, TWO):
            H, M, TH, TM = pool(roster, pol)
            be = breakeven(roster, pol)
            be_r = breakeven(roster, pol, "net_rerun_ns_mean")
            print(f"{lab:>6} {pol:<18} honest {H/1e6:8.1f}  misbehaving {M/1e6:8.1f}  breakeven burn {'none' if be is None else f'{100*be:5.1f}%'}  rerun {'none' if be_r is None else f'{100*be_r:5.1f}%'}")
    print("\nnet time (burn) as % of static time at each share")
    shares = [0, .1, .25, .5, .75, 1.0]
    print("roster/policy              " + "  ".join(f"{int(s*100):>4}%" for s in shares))
    for roster, lab in (("book_v1", "100"), ("book75_v1", "75"), ("book60_v1", "60")):
        for pol in (REC, SLC, TWO):
            print(f"{lab:>4} ms {pol:<18} " + "  ".join(f"{share_curve(roster,pol,m=s):5.2f}" for s in shares))
    print("\nper-program net burn (ms, mean per run) at 100 ms, recommended_slice")
    r = B["book_v1"]["8000"][SLC]
    for x in HON + MIS:
        print(f"  {x:<16} {r[x]['net_burn_ns_mean']/1e6:8.1f}  negative seeds {r[x]['net_burn_negative_seeds']:>2}/20")
    print("\n## 15b predictions")
    H, M, _, _ = pool("book_v1", REC)
    be100 = breakeven("book_v1", REC)
    print(f"B1 H={H/1e6:.1f} ms, M={M/1e6:.1f} ms, breakeven {None if be100 is None else round(100*be100,1)}%: "
          f"{ok(H < 0 < M and be100 is not None and 0.05 <= be100 <= 0.40)}")
    be75 = breakeven("book75_v1", REC)
    neg60 = all(share_curve("book60_v1", REC, m=s) < 0 for s in np.linspace(0, 1, 11))
    print(f"B2 breakeven 75 ms {None if be75 is None else round(100*be75,1)}% (100 ms {None if be100 is None else round(100*be100,1)}%); 60 ms negative everywhere {neg60}: "
          f"{ok(be75 is not None and be100 is not None and be75 - be100 >= 0.15 and neg60)}")
    n_rec0 = share_curve("book_v1", REC, m=0); n_slc0 = share_curve("book_v1", SLC, m=0)
    bs = breakeven("book_v1", SLC)
    print(f"B3 net at 0%: recommended {n_rec0:.2f}, slice {n_slc0:.2f}; breakeven slice {None if bs is None else round(100*bs,1)}%: "
          f"{ok(n_slc0 < n_rec0 and bs is not None and be100 is not None and bs >= be100 - 0.10)}")
    diffs = [abs(share_curve("book_v1", SLC, m=s) - share_curve("book_v1", TWO, m=s)) for s in np.linspace(0, 1, 11)]
    print(f"B4 max |slice - twoway_h16| over shares: {max(diffs):.2f} points: {ok(max(diffs) <= 1)}")
    print("B5 direct mixes vs formula (net burn % of static time):")
    allok = True
    for q, roster in ((.25, "mix25_v1"), (.5, "mix50_v1"), (.75, "mix75_v1")):
        for pol in (REC, SLC, TWO):
            rr = B[roster]["8000"][pol]
            net = sum(v["net_burn_ns_mean"] for v in rr.values()); tot = sum(v["total_ns_static_mean"] for v in rr.values())
            direct = 100 * net / tot; formula = share_curve("book_v1", pol, m=q)
            allok &= abs(direct - formula) <= 1
            print(f"   {int(q*100)}% {pol:<18} direct {direct:6.2f}  formula {formula:6.2f}  diff {direct-formula:+.2f}")
    print(f"   {ok(allok)}")


if __name__ == "__main__":
    table_a(); table_b()
