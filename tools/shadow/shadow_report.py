#!/usr/bin/env python3
"""Build the weekly review list, then score r and q from your labels.
  python3 shadow_report.py review  --deadline 60 --out review.csv     (fill the 'label' column: stuck / working)
  python3 shadow_report.py score   --deadline 60 --labels review.csv
A call is 'long' if it ran over 0.25 x deadline. It 'stalled' if, over some window of 0.1 x deadline, its descendants used
under 2% of one CPU (CPU idle). Concurrent calls are excluded (CPU cannot be attributed to one call)."""
import argparse, csv, json, math, os, random
LOG = os.environ.get("STACK_SHADOW_LOG", os.path.expanduser("~/.stack_shadow/calls.jsonl"))
SAMPLES = os.environ.get("STACK_SHADOW_SAMPLES", os.path.expanduser("~/.stack_shadow/samples.jsonl"))
HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

def read(path):
    out = []
    if os.path.exists(path):
        for line in open(path):
            try: out.append(json.loads(line))
            except ValueError: pass
    return out

def build(deadline, calls_path=LOG, samples_path=SAMPLES):
    starts, ends = {}, {}
    for r in read(calls_path): (starts if r["t"] == "start" else ends)[r["id"]] = r
    samples = {}
    for s in read(samples_path): samples.setdefault(s["id"], []).append(s)
    calls = []
    for i, st in starts.items():
        if i not in ends: continue
        dur = ends[i]["ts"] - st["ts"]; sm = sorted(samples.get(i, []), key=lambda s: s["ts"])
        calls.append({"id": i, "tool": st["tool"], "dur": dur, "long": dur > 0.25 * deadline,
                      "concurrent": any(s["concurrent"] for s in sm), "stalled": stalled(sm, 0.1 * deadline),
                      "quiet": stalled(sm, 0.1 * deadline, "io"), "n": len(sm)})
    return calls

def stalled(sm, window, kind="cpu"):
    """kind 'cpu': over some window the call's processes used under 2% of one CPU (idle). kind 'io': over some window
    they read or wrote no bytes at all (quiet; catches silent busy loops that CPU idleness cannot). A drop in a total
    (a child process ended) counts as activity."""
    for a in range(len(sm)):
        for b in range(a + 1, len(sm)):
            if sm[b]["ts"] - sm[a]["ts"] >= window:
                if kind == "cpu":
                    d = sm[b]["cpu"] - sm[a]["cpu"]
                    if 0 <= d / HZ < 0.02 * (sm[b]["ts"] - sm[a]["ts"]): return True
                elif sm[b].get("io", 0) - sm[a].get("io", 0) == 0:
                    return True
                break
    return False

Z = 2.24   # 97.5% each side: two definitions are scored, so the interval is widened (about 97.5% two-sided each)

def wilson(k, n, z=Z):
    if n == 0: return (0.0, 1.0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)

def score(rows, key="stalled"):
    """rows: dicts with label ('stuck'/'working') and the flag named by key. r = stuck calls flagged; q = working long calls flagged."""
    stuck = [x for x in rows if x["label"] == "stuck"]; work = [x for x in rows if x["label"] == "working"]
    r_k, q_k = sum(x[key] for x in stuck), sum(x[key] for x in work)
    return {"r": (r_k, len(stuck), wilson(r_k, len(stuck))), "q": (q_k, len(work), wilson(q_k, len(work)))}

def verdict(s):
    """Pre-registered: supported only if the whole interval of r is at least 0.6 and the whole interval of q is at most 0.1."""
    return "SUPPORTED" if s["r"][2][0] >= 0.6 and s["q"][2][1] <= 0.1 else ("NOT SUPPORTED" if s["r"][2][1] < 0.6 or s["q"][2][0] > 0.1 else "UNDECIDED (need more labeled calls)")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("mode", choices=["review", "score"]); ap.add_argument("--deadline", type=float, default=60)
    ap.add_argument("--out", default="review.csv"); ap.add_argument("--labels"); ap.add_argument("--sample", type=float, default=0.03)
    a = ap.parse_args(); calls = [c for c in build(a.deadline) if not c["concurrent"]]
    if a.mode == "review":
        rng = random.Random(0)
        pick = [c for c in calls if c["long"] or rng.random() < a.sample]
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["id", "tool", "seconds", "long", "stalled", "label"])
            for c in pick: w.writerow([c["id"], c["tool"], f"{c['dur']:.1f}", c["long"], c["stalled"], ""])
        print(len(pick), "calls to label (stuck or working) in", a.out)
    else:
        by = {c["id"]: c for c in calls}; rows = []
        for r in csv.DictReader(open(a.labels)):
            if r["label"] in ("stuck", "working") and r["id"] in by and by[r["id"]]["long"]:
                rows.append({"label": r["label"], "stalled": by[r["id"]]["stalled"], "quiet": by[r["id"]]["quiet"]})
        for key, name in (("stalled", "CPU idle"), ("quiet", "no bytes read or written")):
            s = score(rows, key)
            print(f"[{name}]")
            print("  r (stuck calls flagged): %d/%d, interval %.2f to %.2f" % (s["r"][0], s["r"][1], *s["r"][2]))
            print("  q (working long calls flagged): %d/%d, interval %.2f to %.2f" % (s["q"][0], s["q"][1], *s["q"][2]))
            print("  verdict:", verdict(s))

if __name__ == "__main__":
    main()
