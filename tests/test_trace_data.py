"""The committed trace test data is intact and matches its manifest (testdata/traces/)."""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "testdata" / "traces"
M = json.loads((D / "MANIFEST.json").read_text())


def test_hashes_match_the_manifest():
    for name, sha in M["sha256"].items():
        assert hashlib.sha256((D / name).read_bytes()).hexdigest() == sha, name


def test_shape_and_columns():
    with open(D / "stack_trace_v1.csv") as f:
        rd = csv.DictReader(f)
        assert rd.fieldnames == ["task_id", "agent_id", "seq", "ts_s", "day_index", "hour_of_day", "team", "host_id",
                                 "kernel_version", "priority", "capability", "input_size_bytes", "duration_ns",
                                 "tokens_requested", "memory_requested_bytes", "retry_count", "expected_load",
                                 "regime", "is_bad"]
        seqs, rows, ids = defaultdict(list), 0, set()
        for r in rd:
            rows += 1; seqs[r["agent_id"]].append(int(r["seq"])); ids.add(r["task_id"])
            assert int(r["duration_ns"]) > 0 and int(r["tokens_requested"]) > 0 and int(r["memory_requested_bytes"]) > 0
            assert r["expected_load"] in ("0", "1") and r["is_bad"] in ("0", "1")
    assert rows == M["rows"] == M["agents"] * M["tasks_per_agent"] and len(ids) == rows
    assert all(s == list(range(M["tasks_per_agent"])) for s in seqs.values())


def test_agent_roster_matches_the_trace():
    agents = list(csv.DictReader(open(D / "stack_agents_v1.csv")))
    assert len(agents) == M["agents"] and {a["should_tighten"] for a in agents} == {"0", "1"}
    with open(D / "stack_trace_v1.csv") as f:
        assert {r["agent_id"] for r in csv.DictReader(f)} == {a["agent_id"] for a in agents}


def test_expected_load_is_only_the_scheduled_batch_agents():
    with open(D / "stack_trace_v1.csv") as f:
        flagged = {r["agent_id"] for r in csv.DictReader(f) if r["expected_load"] == "1"}
    assert flagged == {"batchjob-0", "batchjob-1"}


def test_the_drift_suppression_trace_matches_its_manifest():
    m = json.loads((D / "MANIFEST_inflate_v1.json").read_text())
    for name, sha in m["sha256"].items():
        assert hashlib.sha256((D / name).read_bytes()).hexdigest() == sha, name
    with open(D / "stack_trace_inflate_v1.csv") as f:
        assert sum(1 for _ in f) - 1 == m["rows"] == m["agents"] * m["tasks_per_agent"]
