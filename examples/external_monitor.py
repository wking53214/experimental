"""
Example: bring your own monitoring.

A deliberately plain monitor (rolling z-score on one metric you already collect) that asks the governor
to tighten a limit when the metric looks wrong. Everything the governor guarantees comes from the
governor, not from this monitor: whatever it sends, a limit can only go down, never below the floor,
never more than the circuit breaker allows without a person, and every call is in the audit log.

Standard library only. Run against a server started with, for example:
    ADJUDICATION_SOURCE_TOKENS="monitor=change-me-monitor" ADJUDICATION_OPERATOR_TOKENS="alice=change-me-alice" \
    python scripts/adjudication_server.py 8765
    python examples/external_monitor.py http://127.0.0.1:8765 change-me-monitor api-key-rate
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.error
import urllib.request
import uuid
from collections import deque


def call(base_url, token, method, path, body=None, timeout=5):
    req = urllib.request.Request(base_url + path, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class ZScoreMonitor:
    """Alarm when the last `confirm` values are all more than `k` sd from the rolling mean."""

    def __init__(self, window=200, k=4.0, confirm=5):
        self.history, self.k, self.confirm, self.recent = deque(maxlen=window), k, confirm, deque(maxlen=confirm)

    def update(self, x):
        if len(self.history) >= 30:
            mu, sd = statistics.fmean(self.history), statistics.pstdev(self.history) or 1e-9
            self.recent.append(abs(x - mu) / sd > self.k)
        alarm = len(self.recent) == self.confirm and all(self.recent)
        if not alarm:                      # do not learn from the values that triggered the alarm
            self.history.append(x)
        return alarm


def run(base_url, token, boundary, values, factor=0.9, reset_after=50):
    """Feed values; on each new alarm episode ask for a tightening. Returns the list of replies."""
    mon, replies, quiet, episode = ZScoreMonitor(), [], 0, 0
    for i, x in enumerate(values):
        if mon.update(x):
            if quiet >= reset_after or episode == 0:
                episode += 1
            # one request per alarm episode; the key makes a retry of THIS episode harmless
            key = f"{boundary}-episode-{episode}"
            code, reply = call(base_url, token, "POST", "/signals/tighten", {
                "boundary_id": boundary, "factor": factor, "reason": "metric far from its recent mean",
                "evidence": {"step": i, "value": x}, "idempotency_key": key})
            replies.append((code, reply))
            quiet = 0
        else:
            quiet += 1
    return replies


if __name__ == "__main__":
    import random
    base, tok, bid = sys.argv[1:4]
    rng = random.Random(1)
    series = [rng.gauss(50, 5) for _ in range(300)] + [rng.gauss(95, 5) for _ in range(100)]
    for code, reply in run(base, tok, bid, series):
        print(code, reply.get("status"), reply.get("limit_before"), "->", reply.get("limit_after"), reply.get("reason", ""))
    print(call(base, tok, "GET", f"/boundaries/{bid}")[1])
