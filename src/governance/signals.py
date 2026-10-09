"""
Signal interface: bring your own monitoring.

The detectors in this repository are replaceable. What is not replaceable is the rule that automation
can lower a limit but never raise one. This module is the front door for outside systems (a monitor, an
agent supervisor, a human tool): they say "this looks wrong, tighten", or "please consider loosening",
and every request goes through the same guards as the built-in detectors: the usability floor, the
circuit breaker, the cooldown, the audit log.

  propose_tightening  applies a tighter limit now, or says why not. It can never raise a limit.
  request_loosening   never changes anything: it queues a proposal for a named operator to decide.
  boundary_status     read only.

Policy outcomes come back as a status dict (applied, applied_clamped, held, at_floor, rejected,
duplicate, pending_review). Misuse raises: ValueError for bad input, PermissionError for a source that
cannot authenticate, KeyError for an unknown boundary.
"""
from __future__ import annotations

import json
import math
from collections import OrderedDict
from typing import Any, Optional

from .authority import AuthorizationResult
from .proposal import AdaptationDirection

MAX_EVIDENCE_BYTES = 4096
MAX_REASON_CHARS = 500
MAX_KEY_CHARS = 128
MAX_PENDING_PER_BOUNDARY = 20
MAX_REMEMBERED_KEYS = 10_000


def _finite_number(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError(f"{name} must be a finite number")
    return float(x)


class SignalInterface:
    def __init__(self, governor, sources=None):
        self.gov = governor
        self.sources = sources  # an OperatorRegistry of machine sources, or None
        self._results: "OrderedDict[str, dict]" = OrderedDict()

    # ---------- shared input handling ----------
    def _validate(self, source, reason, evidence, key):
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required")
        if not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS:
            raise ValueError(f"reason must be a string of at most {MAX_REASON_CHARS} characters")
        if key is not None and (not isinstance(key, str) or not key or len(key) > MAX_KEY_CHARS):
            raise ValueError(f"idempotency_key must be a non-empty string of at most {MAX_KEY_CHARS} characters")
        if evidence is not None:
            try:
                size = len(json.dumps(evidence, sort_keys=True))
            except (TypeError, ValueError):
                raise ValueError("evidence must be JSON-serializable")
            if size > MAX_EVIDENCE_BYTES:
                raise ValueError(f"evidence is {size} bytes; the limit is {MAX_EVIDENCE_BYTES}")

    def _authenticate(self, source, credential) -> bool:
        if self.sources is None:
            return False
        if not self.sources.authenticate(source, credential):
            self.gov.audit.append("signal_auth_failed", {"source": source})
            raise PermissionError(f"signal source {source} could not be authenticated")
        return True

    def _remember(self, key, result):
        if key is None:
            return
        self._results[key] = result
        while len(self._results) > MAX_REMEMBERED_KEYS:
            self._results.popitem(last=False)

    def _current_numeric(self, boundary_id):
        try:
            b = self.gov.boundaries.get_boundary(boundary_id)
        except KeyError:
            raise KeyError(f"boundary {boundary_id} not found")
        if isinstance(b.current_limit, bool) or not isinstance(b.current_limit, (int, float)):
            raise ValueError(f"boundary {boundary_id} has a non-numeric limit")
        return b, float(b.current_limit)

    # ---------- tightening ----------
    def propose_tightening(self, boundary_id: str, *, source: str, new_limit: Optional[float] = None,
                           factor: Optional[float] = None, reason: str = "", evidence: Any = None,
                           credential: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict:
        g = self.gov
        self._validate(source, reason, evidence, idempotency_key)
        if (new_limit is None) == (factor is None):
            raise ValueError("give exactly one of new_limit or factor")
        if new_limit is not None:
            new_limit = _finite_number(new_limit, "new_limit")
        else:
            factor = _finite_number(factor, "factor")
            if not 0.0 < factor < 1.0:
                raise ValueError("factor must be strictly between 0 and 1")
        verified = self._authenticate(source, credential)
        if idempotency_key in self._results:
            return {**self._results[idempotency_key], "duplicate": True}

        boundary, current = self._current_numeric(boundary_id)
        target = new_limit if new_limit is not None else current * factor
        base = {"boundary_id": boundary_id, "limit_before": current, "source": source,
                "identity_verified": verified}

        if not target < current:
            res = {**base, "status": "rejected", "limit_after": current,
                   "reason": f"{target} is not lower than the current limit {current}; "
                             "this call can only tighten (use request_loosening to ask a human to raise a limit)"}
            self._remember(idempotency_key, res)
            return res

        history = g.boundaries.boundaries[boundary_id]
        original = history.versions[min(history.versions)].current_limit
        floor = float(original) * 0.20
        clamped = target < floor
        if clamped:
            target = floor
        if not target < current:
            res = {**base, "status": "at_floor", "limit_after": current,
                   "reason": f"already at the usability floor ({floor}); a human must act to change it"}
            self._remember(idempotency_key, res)
            return res

        hold = g._tightening_hold(boundary_id)
        if hold:
            if boundary_id not in g._hold_open:
                g._hold_open.add(boundary_id)
                g.tightening_holds.append({"boundary_id": boundary_id, "reason": hold, "timestamp": g.clock(),
                                           "source": source})
                g.audit.append("tightening_held", {"boundary_id": boundary_id, "reason": hold, "source": source})
            res = {**base, "status": "held", "limit_after": current, "reason": hold}
            self._remember(idempotency_key, res)
            return res
        g._hold_open.discard(boundary_id)

        proposal = g.proposals.create_proposal(
            boundary_id=boundary_id, source_evidence=[], current_value=current, proposed_value=target,
            reason=f"[{source}] {reason}".strip(), direction=AdaptationDirection.TIGHTEN)
        proposal, outcome = g.authorize_proposal(proposal)
        if outcome != AuthorizationResult.AUTO_APPROVED:  # not expected for a numeric tightening
            res = {**base, "status": "rejected", "limit_after": current,
                   "reason": f"the authority did not auto-approve this change ({outcome.value})"}
            self._remember(idempotency_key, res)
            return res

        g._signal_context = {"source": source, "signal_key": idempotency_key, "identity_verified": verified}
        try:
            version = g.apply_approved_proposal(proposal)
        except (ValueError, PermissionError) as e:
            res = {**base, "status": "rejected", "limit_after": current, "reason": str(e)}
            self._remember(idempotency_key, res)
            return res
        finally:
            g._signal_context = None

        g.audit.append("signal", {"kind": "tighten", "source": source, "boundary_id": boundary_id,
                                  "requested": new_limit if new_limit is not None else target,
                                  "applied": target, "clamped_to_floor": clamped, "reason": reason,
                                  "evidence": evidence, "idempotency_key": idempotency_key,
                                  "identity_verified": verified, "proposal_id": proposal.proposal_id})
        used = g._auto_tightenings.get(boundary_id, 0)
        cap = g.max_auto_tightenings
        res = {**base, "status": "applied_clamped" if clamped else "applied", "limit_after": float(version.current_limit),
               "version": version.version, "automatic_tightenings_since_acknowledgement": used,
               "remaining_before_breaker": None if cap is None else max(0, cap - used)}
        self._remember(idempotency_key, res)
        return res

    # ---------- loosening requests (never applied here) ----------
    def request_loosening(self, boundary_id: str, *, requested_by: str, new_limit: float, reason: str = "",
                          evidence: Any = None, credential: Optional[str] = None,
                          idempotency_key: Optional[str] = None) -> dict:
        g = self.gov
        self._validate(requested_by, reason, evidence, idempotency_key)
        new_limit = _finite_number(new_limit, "new_limit")
        verified = self._authenticate(requested_by, credential)
        if idempotency_key in self._results:
            return {**self._results[idempotency_key], "duplicate": True}
        boundary, current = self._current_numeric(boundary_id)
        base = {"boundary_id": boundary_id, "limit_before": current, "source": requested_by,
                "identity_verified": verified, "limit_after": current}
        if not new_limit > current:
            res = {**base, "status": "rejected", "reason": f"{new_limit} is not higher than the current limit {current}"}
            self._remember(idempotency_key, res)
            return res
        pending = [p for p in g.list_pending_review() if p.boundary_id == boundary_id]
        if len(pending) >= MAX_PENDING_PER_BOUNDARY:
            return {**base, "status": "rejected", "reason": f"{len(pending)} requests are already waiting for an operator"}
        proposal = g.proposals.create_proposal(
            boundary_id=boundary_id, source_evidence=[], current_value=current, proposed_value=new_limit,
            reason=f"[{requested_by}] {reason}".strip(), direction=AdaptationDirection.LOOSEN)
        g.submit_for_review(proposal)
        g.audit.append("signal", {"kind": "loosen_requested", "source": requested_by, "boundary_id": boundary_id,
                                  "requested": new_limit, "reason": reason, "evidence": evidence,
                                  "idempotency_key": idempotency_key, "identity_verified": verified,
                                  "proposal_id": proposal.proposal_id})
        res = {**base, "status": "pending_review", "proposal_id": proposal.proposal_id,
               "reason": "waiting for a named operator; nothing has changed"}
        self._remember(idempotency_key, res)
        return res

    # ---------- read only ----------
    def boundary_status(self, boundary_id: str) -> dict:
        g = self.gov
        b, _ = self._current_numeric(boundary_id)
        history = g.boundaries.boundaries[boundary_id]
        original = history.versions[min(history.versions)].current_limit
        cap = g.max_auto_tightenings
        used = g._auto_tightenings.get(boundary_id, 0)
        hold = g._tightening_hold(boundary_id)
        return {"boundary_id": boundary_id, "limit": b.current_limit, "version": b.version,
                "original_limit": original, "usability_floor": float(original) * 0.20,
                "automatic_tightenings_since_acknowledgement": used,
                "remaining_before_breaker": None if cap is None else max(0, cap - used),
                "held": bool(hold), "hold_reason": hold,
                "pending_operator_requests": len([p for p in g.list_pending_review() if p.boundary_id == boundary_id])}
