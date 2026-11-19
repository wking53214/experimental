#!/usr/bin/env python3
"""Local watcher: once a second, for each tool call that has started and not ended, record the total CPU ticks used by the
descendants of its Claude process (Linux /proc). Metadata only. Run it in the background: python3 shadow_watcher.py"""
import json, os, sys, time
LOG = os.environ.get("STACK_SHADOW_LOG", os.path.expanduser("~/.stack_shadow/calls.jsonl"))
SAMPLES = os.environ.get("STACK_SHADOW_SAMPLES", os.path.expanduser("~/.stack_shadow/samples.jsonl"))

def io_bytes(pid):
    """rchar + wchar of a process: bytes it asked to read or write (files, pipes, terminal). 0 if unreadable."""
    try:
        d = dict(l.split(": ") for l in open(f"/proc/{pid}/io").read().strip().splitlines())
        return int(d["rchar"]) + int(d["wchar"])
    except (OSError, ValueError, KeyError):
        return 0

def children_map():
    m = {}
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                st = open(f"/proc/{p}/stat").read()
                rest = st[st.rindex(")") + 2:].split()
                m.setdefault(int(rest[1]), []).append((int(p), int(rest[11]) + int(rest[12]), io_bytes(p)))   # ppid -> (pid, cpu ticks, io bytes)
            except (OSError, ValueError, IndexError):
                continue
    return m

def descendants_cpu(root, m):
    total, io, n, stack = 0, 0, 0, [root]
    while stack:
        for pid, ticks, b in m.get(stack.pop(), []):
            if pid == os.getpid(): continue
            total += ticks; io += b; n += 1; stack.append(pid)
    return total, io, n

def open_calls(offset):
    active, ended = {}, set()
    with open(LOG) as f:
        f.seek(offset)
        for line in f:
            try: r = json.loads(line)
            except ValueError: continue
            (active.__setitem__(r["id"], r) if r["t"] == "start" else ended.add(r["id"]))
        offset = f.tell()
    return active, ended, offset

def main():
    os.makedirs(os.path.dirname(SAMPLES), exist_ok=True)
    live, offset = {}, 0
    while True:
        if os.path.exists(LOG):
            a, e, offset = open_calls(offset)
            live.update(a)
            for i in e: live.pop(i, None)
            m = children_map(); now = time.time()
            with open(SAMPLES, "a") as out:
                for i, r in live.items():
                    ticks, io, n = descendants_cpu(r["ppid"], m)
                    out.write(json.dumps({"id": i, "ts": now, "cpu": ticks, "io": io, "nproc": n, "concurrent": len(live) > 1}) + "\n")
        time.sleep(1.0)

if __name__ == "__main__":
    main()
