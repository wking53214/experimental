"""
Immutable governance principles.

A principle is a policy statement that CANNOT be modified by the adaptive mechanism.
This is the non-negotiable foundation that the governor operates under.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PrincipleType(Enum):
    """Classification of governance principles."""
    RESOURCE_LIMIT = "resource_limit"
    AUTHORITY_RULE = "authority_rule"
    INVARIANT = "invariant"


@dataclass(frozen=True)
class GovernancePrinciple:
    """
    An immutable governance principle.

    Frozen dataclass ensures this cannot be modified after creation.
    The adaptive mechanism can change boundaries, but NOT principles.
    """
    principle_id: str
    name: str
    description: str
    principle_type: PrincipleType
    statement: str
    created_at: float

    def __hash__(self) -> int:
        """Make hashable so it can be stored in sets."""
        return hash((self.principle_id, self.created_at))


# System-level invariants (hardcoded, cannot be overridden)
SYSTEM_INVARIANTS = [
    GovernancePrinciple(
        principle_id="inv_001",
        name="No Automatic Loosening",
        description="Boundaries may never be automatically loosened. Loosening requires explicit human authorization.",
        principle_type=PrincipleType.AUTHORITY_RULE,
        statement="LOOSEN operations require human review",
        created_at=0.0
    ),
    GovernancePrinciple(
        principle_id="inv_002",
        name="No Automatic Disabling",
        description="Boundaries may never be automatically disabled. Disabling requires explicit human authorization.",
        principle_type=PrincipleType.AUTHORITY_RULE,
        statement="DISABLE operations require human review",
        created_at=0.0
    ),
    GovernancePrinciple(
        principle_id="inv_003",
        name="Governance Immutability",
        description="The adaptive mechanism may never modify a governance principle. It may only adjust adaptive boundaries.",
        principle_type=PrincipleType.INVARIANT,
        statement="Governance principles are immutable",
        created_at=0.0
    ),
    GovernancePrinciple(
        principle_id="inv_004",
        name="Historical Evidence Integrity",
        description="Historical execution and violation events must never be rewritten or deleted.",
        principle_type=PrincipleType.INVARIANT,
        statement="Event history is immutable",
        created_at=0.0
    ),
]


class PrincipleStore:
    """
    Stores and enforces governance principles.

    This is write-once: principles are created but never modified.
    """

    def __init__(self):
        self.principles = {}
        # Initialize with system invariants
        for inv in SYSTEM_INVARIANTS:
            self.principles[inv.principle_id] = inv

    def add_principle(self, principle: GovernancePrinciple) -> None:
        """Add a new principle. Cannot overwrite existing principles."""
        if principle.principle_id in self.principles:
            raise ValueError(
                f"Principle {principle.principle_id} already exists. "
                f"Principles are immutable and cannot be replaced."
            )
        self.principles[principle.principle_id] = principle

    def get_principle(self, principle_id: str) -> GovernancePrinciple:
        """Retrieve a principle by ID."""
        if principle_id not in self.principles:
            raise KeyError(f"Principle {principle_id} not found")
        return self.principles[principle_id]

    def list_principles(self) -> list[GovernancePrinciple]:
        """List all principles."""
        return list(self.principles.values())

    def verify_principle_integrity(self) -> bool:
        """
        Verify that system invariants are in place and unmodified.
        This is called before allowing any adaptation.
        """
        for inv in SYSTEM_INVARIANTS:
            stored = self.principles.get(inv.principle_id)
            if stored != inv:
                return False
        return True
