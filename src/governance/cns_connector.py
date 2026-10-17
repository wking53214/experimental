"""Optional connector to CNS (``cns.gate``): make limit records bindable.

This repository is independent of CNS. It has no runtime dependency on it,
imports nothing from it when it loads, and its whole suite passes with CNS
absent. This module is the one place that knows CNS exists, and it asks for CNS
only when a digest is requested. The ``*_content`` functions are pure and work
without CNS; the digest and ``binds`` functions raise :class:`CnsNotInstalled`,
with the install command, when CNS is missing.

Binding records what a limit was. It grants nothing.
----------------------------------------------------
The thesis of this repository is that no observation generated inside the
adaptive loop may grant authority to loosen a constraint: automation may only
tighten, and only a named operator, through an ``AuthorizationGrant`` issued by
the ``AuthorityModel``, may loosen. Nothing here changes that, and nothing here
can. This module only reads records. It never issues, consumes or inspects a
grant, it never calls ``BoundaryStore.update_boundary``, and it never decides a
proposal. A CNS verdict that is bound to a limit record, even a verdict that
passes, is a statement about which record it was issued against: it is not an
approval, it is not a grant, and it does not move any limit. A bound verdict
about a change to a limit does not make that change allowed; ``is_loosening`` and
the grant check in ``update_boundary`` still decide, exactly as before.

What is bound
-------------
* A limit state (:func:`limit_state_content`): a ``BoundaryVersion``. The
  boundary, version, parent version, resource, limit and status.
* A limit change (:func:`limit_change_content`): a mapping in the shape
  ``SignalInterface.propose_tightening`` and ``request_loosening`` return. The
  limit before and after, the direction those two values imply (``tighten``,
  ``loosen`` or ``unchanged``, by the repository's own ``is_loosening`` rule),
  who or what asked (``source``, and whether that identity was verified) and the
  status the change ended in. A held, rejected or merely requested change shows
  as ``unchanged``, because nothing moved.
* An authorization decision (:func:`authorization_content`): an
  ``AuthorizationDecision``. Which proposal, the declared and the implied
  direction, the result, who decided (``system`` or a named operator), whether
  that identity was verified and the grant id, if any.

Left out on purpose: ``BoundaryVersion.created_at`` and
``AuthorizationDecision.timestamp``. After a restart the governor rebuilds both
from the audit log's entry time, not from the instant the object was first
made, so binding them would give a limit a different digest after every
restart. Also left out of a change: the ``duplicate`` retry marker and the
breaker counters, which are process state. A monitor's or an operator's free
text reason is bound as a full SHA-256 digest and never copied.

Install with: ``pip install -r requirements-cns.txt``.
"""
from __future__ import annotations

import hashlib
import importlib
import math
from collections.abc import Mapping
from types import ModuleType
from typing import Any, Optional

from .authority import AuthorizationDecision, AuthorizationResult
from .boundary import BoundaryStatus, BoundaryVersion
from .grant import is_loosening
from .proposal import AdaptationDirection

__all__ = [
    "AUTHORIZATION_SUBJECT",
    "LIMIT_CHANGE_SUBJECT",
    "LIMIT_STATE_SUBJECT",
    "CnsNotInstalled",
    "authorization_content",
    "authorization_digest",
    "binds",
    "cns_available",
    "limit_change_content",
    "limit_change_digest",
    "limit_state_content",
    "limit_state_digest",
]

INSTALL_HINT = "pip install -r requirements-cns.txt"

LIMIT_STATE_SUBJECT = "experimental.limit_state"
LIMIT_CHANGE_SUBJECT = "experimental.limit_change"
AUTHORIZATION_SUBJECT = "experimental.authorization"

UNCHANGED = "unchanged"


class CnsNotInstalled(ImportError):
    """Raised by this module's digest functions when ``cns`` cannot be imported."""


def _cns_gate() -> ModuleType:
    """Import ``cns.gate`` on demand, or say exactly what is missing."""
    try:
        return importlib.import_module("cns.gate")
    except ImportError as exc:
        raise CnsNotInstalled(
            "src.governance.cns_connector needs the CNS package (cns.gate), which is not "
            f"installed. Install it with: {INSTALL_HINT}. The governor itself works "
            "without it."
        ) from exc


def cns_available() -> bool:
    """Whether the CNS gate contract can be imported in this environment."""
    try:
        _cns_gate()
    except CnsNotInstalled:
        return False
    return True


def _str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str; got {type(value).__name__}")
    return value


def _bool(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{what} must be a bool; got {type(value).__name__}")
    return value


def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be an int; got {type(value).__name__}")
    return value


def _maybe(read, value: object, what: str) -> Any:
    return None if value is None else read(value, what)


def _limit(value: object, what: str) -> Any:
    """A limit as it was set: a str, int, float, bool or None. A float must be finite."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{what} must be finite; got {value}")
        return value
    raise TypeError(f"{what} must be a str, number, bool or None; got {type(value).__name__}")


def _enum(value: object, kind: type, what: str) -> str:
    if not isinstance(value, kind):
        raise TypeError(f"{what} must be a {kind.__name__}; got {type(value).__name__}")
    return value.value


def _hash(value: object, what: str) -> str:
    """Full SHA-256 of a free text. Reasons are bound by this and never copied."""
    # surrogatepass: a monitor can send a lone surrogate, which must hash rather than raise
    return hashlib.sha256(_str(value, what).encode("utf-8", "surrogatepass")).hexdigest()


def _direction(before: Any, after: Any) -> str:
    """The direction two limits imply, by the repository's own rule. Nothing moved is ``unchanged``."""
    if type(before) is type(after) and before == after:
        return UNCHANGED
    if is_loosening(before, after):
        return AdaptationDirection.LOOSEN.value
    if not is_loosening(after, before):  # neither is looser: equal numbers of different kinds, such as 5 and 5.0
        return UNCHANGED
    return AdaptationDirection.TIGHTEN.value


def limit_state_content(version: BoundaryVersion) -> dict:
    """The content a verdict on a limit as it stood is bound to. ``created_at`` is not part of it."""
    if not isinstance(version, BoundaryVersion):
        raise TypeError(f"a limit state is a BoundaryVersion; got {type(version).__name__}")
    return {
        "boundary_id": _str(version.boundary_id, "boundary_id"),
        "version": _int(version.version, "version"),
        "parent_version": _maybe(_int, version.parent_version, "parent_version"),
        "resource": _str(version.resource_or_action, "resource_or_action"),
        "limit": _limit(version.current_limit, "current_limit"),
        "status": _enum(version.status, BoundaryStatus, "status"),
    }


def limit_change_content(change: Mapping) -> dict:
    """The content a verdict on a limit change is bound to.

    ``change`` is a mapping like the one ``propose_tightening`` or
    ``request_loosening`` returns. Required: ``boundary_id``, ``limit_before``,
    ``limit_after``, ``source`` and ``status``. Optional: ``identity_verified``,
    ``version``, ``proposal_id`` and ``reason``. Other keys are not bound.
    """
    if not isinstance(change, Mapping):
        raise TypeError(f"a limit change is a mapping; got {type(change).__name__}")
    missing = [key for key in ("boundary_id", "limit_before", "limit_after", "source", "status") if key not in change]
    if missing:
        raise ValueError(f"limit change is missing required key(s): {', '.join(missing)}")
    before = _limit(change["limit_before"], "limit_before")
    after = _limit(change["limit_after"], "limit_after")
    return {
        "boundary_id": _str(change["boundary_id"], "boundary_id"),
        "limit_before": before,
        "limit_after": after,
        "direction": _direction(before, after),
        "source": _str(change["source"], "source"),
        "identity_verified": _maybe(_bool, change.get("identity_verified"), "identity_verified"),
        "status": _str(change["status"], "status"),
        "version": _maybe(_int, change.get("version"), "version"),
        "proposal_id": _maybe(_str, change.get("proposal_id"), "proposal_id"),
        "reason_sha256": _maybe(_hash, change.get("reason"), "reason"),
    }


def authorization_content(decision: AuthorizationDecision) -> dict:
    """The content a verdict on an authority decision is bound to. ``timestamp`` is not part of it."""
    if not isinstance(decision, AuthorizationDecision):
        raise TypeError(f"an authorization is an AuthorizationDecision; got {type(decision).__name__}")
    implied = decision.effective_direction
    return {
        "proposal_id": _str(decision.proposal_id, "proposal_id"),
        "direction": _enum(decision.direction, AdaptationDirection, "direction"),
        "effective_direction": None if implied is None else _enum(implied, AdaptationDirection, "effective_direction"),
        "result": _enum(decision.result, AuthorizationResult, "result"),
        "decided_by": _str(decision.decided_by, "decided_by"),
        "identity_verified": _bool(decision.identity_verified, "identity_verified"),
        "grant_id": _maybe(_str, decision.grant_id, "grant_id"),
        "reason_sha256": _hash(decision.reason, "reason"),
    }


def limit_state_digest(version: BoundaryVersion) -> str:
    """The digest a bound verdict on this limit state carries."""
    return _cns_gate().subject_digest(limit_state_content(version))


def limit_change_digest(change: Mapping) -> str:
    """The digest a bound verdict on this limit change carries."""
    return _cns_gate().subject_digest(limit_change_content(change))


def authorization_digest(decision: AuthorizationDecision) -> str:
    """The digest a bound verdict on this authority decision carries."""
    return _cns_gate().subject_digest(authorization_content(decision))


def binds(result: Any, record: object, *, subject: Optional[str] = None) -> bool:
    """True iff ``result`` (a ``cns.gate.GateResult``) was issued against exactly this record.

    ``record`` is a ``BoundaryVersion``, a limit-change mapping or an
    ``AuthorizationDecision``; ``subject`` defaults to the matching label. A
    verdict that records nothing it judged (unbound) never binds. Binding says
    which record a verdict was about. It grants nothing.
    """
    gate = _cns_gate()
    if not isinstance(result, gate.GateResult):
        raise TypeError(f"binds takes a cns.gate.GateResult; got {type(result).__name__}")
    if isinstance(record, BoundaryVersion):
        default, digest = LIMIT_STATE_SUBJECT, limit_state_digest(record)
    elif isinstance(record, AuthorizationDecision):
        default, digest = AUTHORIZATION_SUBJECT, authorization_digest(record)
    elif isinstance(record, Mapping):
        default, digest = LIMIT_CHANGE_SUBJECT, limit_change_digest(record)
    else:
        raise TypeError(
            "binds takes a BoundaryVersion, an AuthorizationDecision or a limit-change mapping; "
            f"got {type(record).__name__}")
    return result.binds(default if subject is None else subject, digest)
