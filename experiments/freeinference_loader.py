"""Loader for the FreeInference Agentic Trace (CC-BY-4.0, Harvard MadSys). Returns the same structure as class_budget_round21.load:
{user: (latency_ms array, tool-name array)}. Rules are pre-registered in docs/TRACE_TEST_PLAN.md (outside-data replication).
Input: a directory holding <week>_tool_calls.parquet, <week>_sessions.parquet and req_times.parquet (session_id, step, start_ms)."""
import glob, os
import numpy as np
import pandas as pd


def load(path):
    calls = pd.concat([pd.read_parquet(p, columns=["session_id", "step", "call_index", "name", "latency_ms"])
                       for p in sorted(glob.glob(os.path.join(path, "*_tool_calls.parquet")))])
    sess = pd.concat([pd.read_parquet(p, columns=["session_id", "user_id"]) for p in glob.glob(os.path.join(path, "*_sessions.parquet"))]).drop_duplicates("session_id")
    req = pd.read_parquet(os.path.join(path, "req_times.parquet"), columns=["session_id", "step", "start_ms"]).drop_duplicates(["session_id", "step"])
    d = calls.merge(sess, on="session_id").merge(req, on=["session_id", "step"], how="left")
    d = d[d.latency_ms.notna() & (d.latency_ms >= 0) & d.start_ms.notna()]
    d = d.sort_values(["user_id", "start_ms", "step", "call_index"], kind="stable")
    out = {}
    for u, g in d.groupby("user_id", sort=False):
        out[u] = (g.latency_ms.to_numpy(float), g.name.fillna("none").to_numpy(dtype=object))
    return out
