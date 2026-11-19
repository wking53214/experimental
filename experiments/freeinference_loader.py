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


PREFIX = {"cd", "echo", "export", "set", "source", "true", "pwd"}


def main_program(programs):
    """Pre-registered rule (docs/TRACE_TEST_PLAN.md, class split): first token of the first program not in PREFIX, else ''."""
    if programs is None:
        return ""
    for p in programs:
        t = str(p).split()
        if t and t[0] not in PREFIX:
            return t[0]
    return ""


def load_split(path):
    """Like load(), but each user maps to (latency_ms, name, name|program) in the same order."""
    calls = pd.concat([pd.read_parquet(p, columns=["session_id", "step", "call_index", "name", "programs", "latency_ms"])
                       for p in sorted(glob.glob(os.path.join(path, "*_tool_calls.parquet")))])
    sess = pd.concat([pd.read_parquet(p, columns=["session_id", "user_id"]) for p in glob.glob(os.path.join(path, "*_sessions.parquet"))]).drop_duplicates("session_id")
    req = pd.read_parquet(os.path.join(path, "req_times.parquet"), columns=["session_id", "step", "start_ms"]).drop_duplicates(["session_id", "step"])
    d = calls.merge(sess, on="session_id").merge(req, on=["session_id", "step"], how="left")
    d = d[d.latency_ms.notna() & (d.latency_ms >= 0) & d.start_ms.notna()]
    d = d.sort_values(["user_id", "start_ms", "step", "call_index"], kind="stable")
    d["nm"] = d.name.fillna("none"); d["sub"] = d.nm + "|" + d.programs.map(main_program)
    return {u: (g.latency_ms.to_numpy(float), g.nm.to_numpy(dtype=object), g["sub"].to_numpy(dtype=object)) for u, g in d.groupby("user_id", sort=False)}


def load_real(path):
    """Like load(), plus the real outcome: {user: (latency_ms, name, outcome)}; calls with missing outcome dropped. Used by the purple-team stage 1."""
    calls = pd.concat([pd.read_parquet(p, columns=["session_id", "step", "call_index", "name", "outcome", "latency_ms"])
                       for p in sorted(glob.glob(os.path.join(path, "*_tool_calls.parquet")))])
    sess = pd.concat([pd.read_parquet(p, columns=["session_id", "user_id"]) for p in glob.glob(os.path.join(path, "*_sessions.parquet"))]).drop_duplicates("session_id")
    req = pd.read_parquet(os.path.join(path, "req_times.parquet"), columns=["session_id", "step", "start_ms"]).drop_duplicates(["session_id", "step"])
    d = calls.merge(sess, on="session_id").merge(req, on=["session_id", "step"], how="left")
    d = d[d.latency_ms.notna() & (d.latency_ms >= 0) & d.start_ms.notna() & d.outcome.notna()]
    d = d.sort_values(["user_id", "start_ms", "step", "call_index"], kind="stable")
    return {u: (g.latency_ms.to_numpy(float), g.name.fillna("none").to_numpy(dtype=object), g.outcome.to_numpy(dtype=object))
            for u, g in d.groupby("user_id", sort=False)}
