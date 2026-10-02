"""
Execution and violation events.

These form the immutable evidence record of system behavior.
Historical events are never rewritten, even if boundaries change.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time
import uuid


class ExecutionOutcome(Enum):
    """Outcome of an execution attempt."""
    COMPLIANT = "compliant"
    VIOLATION = "violation"


@dataclass
class ExecutionEvent:
    """A single execution attempt against a boundary."""
    execution_id: str
    boundary_id: str
    boundary_version: int
    observed_value: Any
    timestamp: float
    context: dict = field(default_factory=dict)
    outcome: ExecutionOutcome = ExecutionOutcome.COMPLIANT

    def __hash__(self) -> int:
        return hash(self.execution_id)


@dataclass
class ViolationEvent:
    """
    An immutable record of a boundary violation.

    This event preserves enough information to reconstruct exactly what happened,
    which boundary was active, and when.
    """
    violation_id: str
    execution_id: str
    boundary_id: str
    boundary_version: int
    observed_value: Any
    limit_value: Any
    timestamp: float
    context: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.violation_id)


class EventStore:
    """
    Immutable event store.

    Events are write-once: recorded but never modified or deleted.
    This ensures historical accuracy even as boundaries change.
    """

    def __init__(self):
        self.executions: dict[str, ExecutionEvent] = {}
        self.violations: dict[str, ViolationEvent] = {}
        self._execution_order = []
        self._violation_order = []

    def record_execution(
        self,
        boundary_id: str,
        boundary_version: int,
        observed_value: Any,
        context: Optional[dict] = None,
    ) -> ExecutionEvent:
        """Record an execution attempt."""
        event = ExecutionEvent(
            execution_id=str(uuid.uuid4()),
            boundary_id=boundary_id,
            boundary_version=boundary_version,
            observed_value=observed_value,
            timestamp=time.time(),
            context=context or {},
            outcome=ExecutionOutcome.COMPLIANT,
        )
        self.executions[event.execution_id] = event
        self._execution_order.append(event.execution_id)
        return event

    def record_violation(
        self,
        execution_id: str,
        boundary_id: str,
        boundary_version: int,
        observed_value: Any,
        limit_value: Any,
        context: Optional[dict] = None,
    ) -> ViolationEvent:
        """Record a boundary violation."""
        # Mark the execution as a violation
        if execution_id in self.executions:
            self.executions[execution_id].outcome = ExecutionOutcome.VIOLATION

        # Create immutable violation event
        event = ViolationEvent(
            violation_id=str(uuid.uuid4()),
            execution_id=execution_id,
            boundary_id=boundary_id,
            boundary_version=boundary_version,
            observed_value=observed_value,
            limit_value=limit_value,
            timestamp=time.time(),
            context=context or {},
        )
        self.violations[event.violation_id] = event
        self._violation_order.append(event.violation_id)
        return event

    def get_execution(self, execution_id: str) -> ExecutionEvent:
        """Retrieve an execution event."""
        if execution_id not in self.executions:
            raise KeyError(f"Execution {execution_id} not found")
        return self.executions[execution_id]

    def get_violation(self, violation_id: str) -> ViolationEvent:
        """Retrieve a violation event."""
        if violation_id not in self.violations:
            raise KeyError(f"Violation {violation_id} not found")
        return self.violations[violation_id]

    def get_violations_for_boundary(self, boundary_id: str) -> list[ViolationEvent]:
        """Get all violations for a specific boundary, in order."""
        return [
            self.violations[vid] for vid in self._violation_order
            if self.violations[vid].boundary_id == boundary_id
        ]

    def get_violations_in_time_window(
        self,
        boundary_id: str,
        start_time: float,
        end_time: float,
    ) -> list[ViolationEvent]:
        """Get violations within a time window."""
        return [
            v for v in self.get_violations_for_boundary(boundary_id)
            if start_time <= v.timestamp <= end_time
        ]

    def get_all_violations(self) -> list[ViolationEvent]:
        """Get all violations in chronological order."""
        return [self.violations[vid] for vid in self._violation_order]

    def get_all_executions(self) -> list[ExecutionEvent]:
        """Get all executions in chronological order."""
        return [self.executions[eid] for eid in self._execution_order]
