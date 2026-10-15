"""
Authorization grants: the only way to raise a boundary limit.

Raising a limit (a loosening) needs a grant issued by the AuthorityModel when a human
operator approves. A grant is tied to one proposal, one boundary and one value, and can
be used once. BoundaryStore checks it against the boundary's actual current limit, so a
proposal that mislabels its own direction or current value cannot loosen anything.

Scope: this stops bugs and misuse of the public API. Python cannot stop malicious code
running in the same process from forging a grant; the version-history audit in
BoundaryStore exists to make that visible afterwards.
"""
from __future__ import annotations

import math
from fractions import Fraction
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

_ISSUER_KEY = object()  # only AuthorityModel imports this


def is_loosening(current: Any, new: Any) -> bool:
    """True unless `new` is provably no looser than `current`.

    Anything that is not a pair of comparable, non-NaN numbers counts as loosening,
    because it cannot be shown to be a tightening. The comparison is exact: two
    integers that round to the same double (10**18 and 10**18 + 50) are not equal
    here, so a raise below the resolution of a float still needs a grant (found by
    the red team, docs/RED_TEAM.md S1).
    """
    if isinstance(current, bool) or isinstance(new, bool):
        return True
    try:
        c, n = _exact(current), _exact(new)
    except (TypeError, ValueError, OverflowError):
        return True
    if c is None or n is None:
        return True
    return n > c


def _exact(v: Any):
    """An exact rational for ints, floats, Decimals and numeric strings; None for NaN or an infinity."""
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return Fraction(v)
    if isinstance(v, str):
        v = v.strip()
        if v.lower() in ("nan", "inf", "+inf", "-inf", "infinity", "-infinity", "+infinity"):
            return None
        return Fraction(v)
    f = float(v)                     # Decimal, numpy scalars
    if math.isnan(f) or math.isinf(f):
        return None
    return Fraction(v)


@dataclass
class AuthorizationGrant:
    _key: object = field(repr=False)
    proposal_id: str
    boundary_id: str
    new_limit: Any
    operator_id: str
    grant_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    used: bool = False

    def __post_init__(self):
        if self._key is not _ISSUER_KEY:
            raise PermissionError("AuthorizationGrant can only be issued by AuthorityModel")

    def consume(self, boundary_id: str, new_limit: Any) -> None:
        if self.used:
            raise PermissionError(f"grant {self.grant_id} was already used")
        if boundary_id != self.boundary_id or new_limit != self.new_limit:
            raise PermissionError(
                f"grant {self.grant_id} authorizes {self.boundary_id} -> {self.new_limit}, "
                f"not {boundary_id} -> {new_limit}")
        self.used = True
