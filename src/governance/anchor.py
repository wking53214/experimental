"""
Anchor store for the audit log (docs/THREAT_MODEL.md, T8).

The hash chain makes edits to the audit log evident only against a trusted copy of a recent head: someone who can
rewrite the whole log can recompute every hash. An anchor store keeps that copy somewhere the governor's process
cannot rewrite. This module gives the governor the two calls it needs and one implementation:

    store.publish(anchor)   after every audit append (fail closed: if the anchor cannot be stored, the append fails
                            and the change it records does not happen)
    store.latest()          at startup; the log must still contain that history or the governor refuses to start

FileAnchorStore writes one JSON line per anchor to a file opened in append mode. What the file gives you depends on
where you put it. On the governor's own host, under the governor's own user, it gives nothing: whoever rewrites the
log rewrites the anchors (tested, `tests/test_audit_anchor.py`). It helps when the path is one the process can
append to but not truncate or rewrite: a file owned by another user with the append-only attribute set
(`chattr +a` on Linux), a mount that only accepts appends, or a remote service behind the same two calls. The
store is a hook for that placement, not a substitute for it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional


class AnchorStoreError(RuntimeError):
    """The anchor could not be stored or read."""


class FileAnchorStore:
    def __init__(self, path: str):
        self.path = path

    def publish(self, anchor: dict) -> None:
        if not isinstance(anchor, dict) or not isinstance(anchor.get("length"), int) or not isinstance(anchor.get("head"), str):
            raise AnchorStoreError("malformed anchor")
        line = json.dumps({"length": anchor["length"], "head": anchor["head"], "ts": time.time()},
                          sort_keys=True, separators=(",", ":"))
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            raise AnchorStoreError(f"cannot store the audit anchor at {self.path}: {e}") from e

    def latest(self) -> Optional[dict]:
        """The most recent anchor, or None if none was ever stored. A store that exists but cannot be read is an
        error, not an absence: starting without the anchor would silently drop the check."""
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().split("\n") if l.strip()]
            if not lines:
                return None
            last = json.loads(lines[-1])
            if not isinstance(last.get("length"), int) or not isinstance(last.get("head"), str):
                raise AnchorStoreError("the latest anchor is malformed")
            return {"length": last["length"], "head": last["head"]}
        except (OSError, ValueError) as e:
            raise AnchorStoreError(f"cannot read the audit anchor at {self.path}: {e}") from e
