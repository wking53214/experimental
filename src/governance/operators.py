"""
Operator registry: who counts as a human operator, and proof that a caller is one.

Credentials are never stored, only a salted PBKDF2-SHA256 hash. authenticate() compares in
constant time. Used by AuthorityModel: when a registry is configured, an operator decision
(the only way to loosen a limit) is refused unless the credential authenticates for that
operator_id.

Limits: this is a shared-secret scheme held in one process, so it proves "the caller knew the
secret for alice", not non-repudiation (the process could forge any registered operator's
decision). Same-process code is outside the threat model (A4).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

_ITERATIONS = 120_000


class OperatorRegistry:
    def __init__(self, iterations: Optional[int] = None, min_length: int = 8):
        self._records: dict = {}
        self._iterations = iterations or _ITERATIONS
        self.min_length = min_length

    def register(self, operator_id: str, credential: str) -> None:
        if not operator_id or not str(operator_id).strip():
            raise ValueError("operator_id is required")
        if not credential or len(credential) < self.min_length:
            raise ValueError(f"credential must be at least {self.min_length} characters")
        if operator_id in self._records:
            raise ValueError(f"operator {operator_id} already registered")
        salt = os.urandom(16)
        self._records[operator_id] = (salt, self._derive(credential, salt))

    def revoke(self, operator_id: str) -> None:
        self._records.pop(operator_id, None)

    def is_registered(self, operator_id: str) -> bool:
        return operator_id in self._records

    def authenticate(self, operator_id: str, credential: Optional[str]) -> bool:
        record = self._records.get(operator_id)
        if record is None or not credential:
            # do equivalent work for unknown operators so timing does not reveal who exists
            self._derive(credential or "x", b"\0" * 16)
            return False
        salt, expected = record
        return hmac.compare_digest(self._derive(credential, salt), expected)

    def identify(self, credential: Optional[str]) -> Optional[str]:
        """Which registered operator does this credential belong to, if any."""
        if not credential:
            return None
        found = None
        for operator_id, (salt, expected) in self._records.items():
            if hmac.compare_digest(self._derive(credential, salt), expected):
                found = operator_id
        return found

    def _derive(self, credential: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", credential.encode("utf-8"), salt, self._iterations)
