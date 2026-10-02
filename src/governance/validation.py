"""
Post-adaptation validation.

After a boundary is changed, validation determines if the change achieved
the intended effect.

Validation is categorical (IMPROVED/UNCHANGED/DEGRADED/UNKNOWN), not score-based.
The system does not invent effectiveness metrics.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any
import time
import uuid


class ValidationOutcome(Enum):
    """Result of post-adaptation validation."""
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class ValidationResult:
    """
    Record of validation after an adaptation.

    Captures: what was expected, what was observed, what was the outcome.
    """
    validation_id: str
    proposal_id: str
    boundary_id: str
    timestamp: float
    expected_condition: str
    observed_condition: str
    outcome: ValidationOutcome
    notes: dict

    def __hash__(self) -> int:
        return hash(self.validation_id)


class ValidatorOracle:
    """
    A validation oracle that determines the outcome of an adaptation.

    Phase 1 requires explicit oracle functions: no guessing, no inference.
    """

    def __init__(self):
        self.validators: dict[str, Callable] = {}

    def register_validator(
        self,
        boundary_id: str,
        validator_func: Callable[[Any], ValidationOutcome],
    ) -> None:
        """
        Register a validator function for a boundary.

        The validator is called with the boundary's current state
        and returns a ValidationOutcome.
        """
        self.validators[boundary_id] = validator_func

    def validate(
        self,
        boundary_id: str,
        observed_state: Any,
    ) -> ValidationOutcome:
        """
        Validate the current state of a boundary.

        If no validator is registered, returns UNKNOWN.
        """
        if boundary_id not in self.validators:
            return ValidationOutcome.UNKNOWN

        try:
            outcome = self.validators[boundary_id](observed_state)
            if not isinstance(outcome, ValidationOutcome):
                return ValidationOutcome.UNKNOWN
            return outcome
        except Exception:
            return ValidationOutcome.UNKNOWN


class ValidationStore:
    """
    Records validation results after each adaptation.

    Immutable: validation results are recorded but never modified.
    """

    def __init__(self):
        self.results: dict[str, ValidationResult] = {}
        self._by_proposal = {}
        self._by_boundary = {}

    def record_validation(
        self,
        proposal_id: str,
        boundary_id: str,
        expected_condition: str,
        observed_condition: str,
        outcome: ValidationOutcome,
        notes: Optional[dict] = None,
    ) -> ValidationResult:
        """Record a validation result."""
        result = ValidationResult(
            validation_id=str(uuid.uuid4()),
            proposal_id=proposal_id,
            boundary_id=boundary_id,
            timestamp=time.time(),
            expected_condition=expected_condition,
            observed_condition=observed_condition,
            outcome=outcome,
            notes=notes or {},
        )

        self.results[result.validation_id] = result

        if proposal_id not in self._by_proposal:
            self._by_proposal[proposal_id] = []
        self._by_proposal[proposal_id].append(result.validation_id)

        if boundary_id not in self._by_boundary:
            self._by_boundary[boundary_id] = []
        self._by_boundary[boundary_id].append(result.validation_id)

        return result

    def get_validation(self, validation_id: str) -> ValidationResult:
        """Retrieve a validation result."""
        if validation_id not in self.results:
            raise KeyError(f"Validation {validation_id} not found")
        return self.results[validation_id]

    def get_validations_for_proposal(self, proposal_id: str) -> list[ValidationResult]:
        """Get all validations for a proposal."""
        if proposal_id not in self._by_proposal:
            return []
        return [
            self.results[vid] for vid in self._by_proposal[proposal_id]
        ]

    def get_validations_for_boundary(self, boundary_id: str) -> list[ValidationResult]:
        """Get all validations for a boundary."""
        if boundary_id not in self._by_boundary:
            return []
        return [
            self.results[vid] for vid in self._by_boundary[boundary_id]
        ]

    def get_all_validations(self) -> list[ValidationResult]:
        """Get all validation results."""
        return list(self.results.values())
