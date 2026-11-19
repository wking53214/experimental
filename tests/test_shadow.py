import json, os, subprocess, sys, time
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "shadow"))
import shadow_report as sr

HOOK = str(Path(__file__).resolve().parents[1] / "tools" / "shadow" / "shadow_hook.py")
WATCH = str(Path(__file__).resolve().parents[1] / "tools" / "shadow" / "shadow_watcher.py")


def hook(tmp, payload):
    env = {**os.environ, "STACK_SHADOW_LOG": str(tmp / "calls.jsonl")}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True, capture_output=True, env=env)
    return r


def test_hook_stores_metadata_only(tmp_path):
    r = hook(tmp_path, {"hook_event_name": "PreToolUse", "tool_use_id": "t1", "session_id": "S", "tool_name": "Bash",
                        "tool_input": {"command": "cat /secret/passwords.txt"}})
    assert r.returncode == 0 and r.stdout == ""
    text = (tmp_path / "calls.jsonl").read_text()
    assert "secret" not in text and "passwords" not in text and "Bash" in text and '"S"' not in text


def test_hook_never_fails_on_junk(tmp_path):
    env = {**os.environ, "STACK_SHADOW_LOG": str(tmp_path / "calls.jsonl")}
    r = subprocess.run([sys.executable, HOOK], input="not json", text=True, capture_output=True, env=env)
    assert r.returncode == 0 and r.stdout == ""


def mk(tmp, rows_calls, rows_samples):
    (tmp / "c.jsonl").write_text("\n".join(json.dumps(x) for x in rows_calls))
    (tmp / "s.jsonl").write_text("\n".join(json.dumps(x) for x in rows_samples))
    return sr.build(60, str(tmp / "c.jsonl"), str(tmp / "s.jsonl"))


def test_idle_long_call_is_stalled_and_busy_is_not(tmp_path):
    calls = [{"t": "start", "id": "a", "ts": 0, "tool": "Bash", "ppid": 1}, {"t": "end", "id": "a", "ts": 40, "tool": "Bash"},
             {"t": "start", "id": "b", "ts": 0, "tool": "Bash", "ppid": 1}, {"t": "end", "id": "b", "ts": 40, "tool": "Bash"}]
    sam = [{"id": "a", "ts": t, "cpu": 5, "nproc": 1, "concurrent": False} for t in range(0, 40)]
    sam += [{"id": "b", "ts": t, "cpu": 100 * t, "nproc": 1, "concurrent": False} for t in range(0, 40)]
    c = {x["id"]: x for x in mk(tmp_path, calls, sam)}
    assert c["a"]["long"] and c["a"]["stalled"] and not c["b"]["stalled"]


def test_wilson_and_verdict():
    s = sr.score([{"label": "stuck", "stalled": True}] * 40 + [{"label": "working", "stalled": False}] * 100)
    assert sr.verdict(s) == "SUPPORTED"
    s = sr.score([{"label": "stuck", "stalled": True}] * 3 + [{"label": "working", "stalled": False}] * 10)
    assert sr.verdict(s).startswith("UNDECIDED")
    s = sr.score([{"label": "stuck", "stalled": False}] * 40 + [{"label": "working", "stalled": True}] * 40)
    assert sr.verdict(s) == "NOT SUPPORTED"


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="needs Linux /proc")
def test_watcher_samples_a_running_call(tmp_path):
    log, sam = tmp_path / "calls.jsonl", tmp_path / "samples.jsonl"
    parent = subprocess.Popen(["sh", "-c", "sleep 6"])
    log.write_text(json.dumps({"t": "start", "id": "x", "ts": time.time(), "tool": "Bash", "ppid": parent.pid}) + "\n")
    w = subprocess.Popen([sys.executable, WATCH], env={**os.environ, "STACK_SHADOW_LOG": str(log), "STACK_SHADOW_SAMPLES": str(sam)})
    time.sleep(3.2); w.kill(); parent.kill(); w.wait(); parent.wait()
    rows = [json.loads(l) for l in sam.read_text().splitlines()]
    assert len(rows) >= 2 and rows[0]["id"] == "x" and rows[0]["nproc"] >= 1 and "io" in rows[0]


def test_quiet_catches_silent_spin_that_idle_misses(tmp_path):
    calls = [{"t": "start", "id": "a", "ts": 0, "tool": "Bash", "ppid": 1}, {"t": "end", "id": "a", "ts": 40, "tool": "Bash"}]
    sam = [{"id": "a", "ts": t, "cpu": 100 * t, "io": 5000, "nproc": 1, "concurrent": False} for t in range(0, 40)]   # burning CPU, no I/O
    c = mk(tmp_path, calls, sam)[0]
    assert not c["stalled"] and c["quiet"]


def test_busy_with_io_is_neither(tmp_path):
    calls = [{"t": "start", "id": "a", "ts": 0, "tool": "Bash", "ppid": 1}, {"t": "end", "id": "a", "ts": 40, "tool": "Bash"}]
    sam = [{"id": "a", "ts": t, "cpu": 100 * t, "io": 5000 + 900 * t, "nproc": 1, "concurrent": False} for t in range(0, 40)]
    c = mk(tmp_path, calls, sam)[0]
    assert not c["stalled"] and not c["quiet"]


def test_child_exit_counts_as_activity(tmp_path):
    calls = [{"t": "start", "id": "a", "ts": 0, "tool": "Bash", "ppid": 1}, {"t": "end", "id": "a", "ts": 40, "tool": "Bash"}]
    sam = [{"id": "a", "ts": t, "cpu": 500 - 10 * t, "io": 9000 - 100 * t, "nproc": 1, "concurrent": False} for t in range(0, 40)]
    c = mk(tmp_path, calls, sam)[0]
    assert not c["stalled"] and not c["quiet"]
