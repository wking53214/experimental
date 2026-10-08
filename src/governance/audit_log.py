"""
Hash-chained, append-only audit log.

Each entry commits to the one before it: hash = SHA-256(previous_hash + canonical JSON of the
entry). Editing, deleting, inserting or reordering any past entry breaks every later hash, which
verify() reports with the index of the first bad entry.

What this does and does not give you (see docs/THREAT_MODEL.md, T8):
  * It detects edits to past entries, and cut-and-splice, as long as you hold a trusted copy of a
    recent head hash (an "anchor") taken earlier. Anchor it somewhere the writer cannot reach.
  * Without an anchor it detects only inconsistent edits. Someone who can rewrite the whole log
    can recompute every hash, and it will look valid. A chain is tamper-EVIDENT, not tamper-proof.
  * Truncating the tail is detected only against an anchor that is longer than the truncated log.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

GENESIS = "0" * 64


class AuditIntegrityError(RuntimeError):
    """The log failed verification, or replaying it would break a safety rule."""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(prev_hash: str, seq: int, ts: float, kind: str, payload: Any) -> str:
    body = _canonical({"seq": seq, "ts": ts, "kind": kind, "payload": payload})
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.truncated_tail = False
        self.entries: list = []
        if path and os.path.exists(path):
            self._load(path)

    # ---- writing ----
    def append(self, kind: str, payload: Any) -> dict:
        payload = json.loads(_canonical(payload))  # freeze to plain JSON now, not at verify time
        seq = len(self.entries)
        prev = self.entries[-1]["hash"] if self.entries else GENESIS
        ts = time.time()
        entry = {"seq": seq, "ts": ts, "kind": kind, "payload": payload, "prev": prev,
                 "hash": _entry_hash(prev, seq, ts, kind, payload)}
        self.entries.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(_canonical(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return entry

    # ---- reading ----
    @property
    def head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else GENESIS

    def anchor(self) -> dict:
        """A small value to store somewhere the log's writer cannot modify."""
        return {"length": len(self.entries), "head": self.head}

    def verify(self, anchor: Optional[dict] = None) -> tuple:
        """Returns (ok, problem). problem names the first bad entry or the anchor mismatch."""
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e.get("seq") != i:
                return False, f"entry {i}: sequence number is {e.get('seq')}"
            if e.get("prev") != prev:
                return False, f"entry {i}: does not follow the previous entry"
            if _entry_hash(prev, i, e.get("ts"), e.get("kind"), e.get("payload")) != e.get("hash"):
                return False, f"entry {i}: contents do not match its hash"
            prev = e["hash"]
        if anchor is not None:
            n = anchor["length"]
            if len(self.entries) < n:
                return False, f"log has {len(self.entries)} entries but the anchor was taken at {n}"
            at = self.entries[n - 1]["hash"] if n else GENESIS
            if at != anchor["head"]:
                return False, f"history before entry {n} differs from the anchored history"
        return True, None

    def find(self, kind: Optional[str] = None) -> list:
        return [e for e in self.entries if kind is None or e["kind"] == kind]

    def _load(self, path: str) -> None:
        """Read the file. A final line that does not parse is a write that was cut off by a crash:
        it never became durable, so it is dropped and the file is repaired. A bad line anywhere
        else is corruption and raises."""
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().split("\n") if l.strip()]
        for i, line in enumerate(lines):
            try:
                self.entries.append(json.loads(line))
            except ValueError:
                if i != len(lines) - 1:
                    raise AuditIntegrityError(f"line {i + 1} of {path} is corrupt")
                self.truncated_tail = True
                tmp = path + ".repair"
                with open(tmp, "w", encoding="utf-8") as g:
                    g.write("".join(_canonical(e) + "\n" for e in self.entries))
                    g.flush()
                    os.fsync(g.fileno())
                os.replace(tmp, path)
