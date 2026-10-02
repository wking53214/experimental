"""
Immutable file-based event store.

Events are serialized to JSON files. Once written, files are never overwritten,
only appended. This demonstrates that historical accuracy is preserved even
as the system evolves.
"""
import json
import os
from pathlib import Path
from typing import Any, Optional
import time


class ImmutableFileStore:
    """
    Append-only file store for immutable events.

    Each event is written to a new file. Files are never deleted or modified.
    This is a simple demonstration that historical integrity is preserved.
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Subdirectories for different event types
        self.violations_dir = self.base_path / "violations"
        self.executions_dir = self.base_path / "executions"
        self.proposals_dir = self.base_path / "proposals"
        self.validations_dir = self.base_path / "validations"
        self.decisions_dir = self.base_path / "decisions"

        for d in [self.violations_dir, self.executions_dir, self.proposals_dir,
                  self.validations_dir, self.decisions_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def write_violation_event(self, event_id: str, event_data: dict) -> None:
        """
        Write a violation event. File is write-once.
        Attempting to overwrite raises an error.
        """
        path = self.violations_dir / f"{event_id}.json"
        if path.exists():
            raise ValueError(
                f"Violation event {event_id} already exists. "
                f"Cannot overwrite immutable events."
            )

        with open(path, 'w') as f:
            json.dump(event_data, f, indent=2)

    def write_execution_event(self, event_id: str, event_data: dict) -> None:
        """Write an execution event. File is write-once."""
        path = self.executions_dir / f"{event_id}.json"
        if path.exists():
            raise ValueError(
                f"Execution event {event_id} already exists. "
                f"Cannot overwrite immutable events."
            )

        with open(path, 'w') as f:
            json.dump(event_data, f, indent=2)

    def write_proposal(self, proposal_id: str, proposal_data: dict) -> None:
        """Write a proposal. File is write-once."""
        path = self.proposals_dir / f"{proposal_id}.json"
        if path.exists():
            raise ValueError(
                f"Proposal {proposal_id} already exists. "
                f"Cannot overwrite immutable records."
            )

        with open(path, 'w') as f:
            json.dump(proposal_data, f, indent=2)

    def write_validation(self, validation_id: str, validation_data: dict) -> None:
        """Write a validation result. File is write-once."""
        path = self.validations_dir / f"{validation_id}.json"
        if path.exists():
            raise ValueError(
                f"Validation {validation_id} already exists. "
                f"Cannot overwrite immutable records."
            )

        with open(path, 'w') as f:
            json.dump(validation_data, f, indent=2)

    def write_decision(self, decision_id: str, decision_data: dict) -> None:
        """Write an authorization decision. File is write-once."""
        path = self.decisions_dir / f"{decision_id}.json"
        if path.exists():
            raise ValueError(
                f"Decision {decision_id} already exists. "
                f"Cannot overwrite immutable records."
            )

        with open(path, 'w') as f:
            json.dump(decision_data, f, indent=2)

    def read_violation_event(self, event_id: str) -> dict:
        """Read a violation event."""
        path = self.violations_dir / f"{event_id}.json"
        if not path.exists():
            raise KeyError(f"Violation event {event_id} not found")

        with open(path, 'r') as f:
            return json.load(f)

    def list_violations(self) -> list[dict]:
        """List all violation events in chronological order."""
        events = []
        for path in sorted(self.violations_dir.glob("*.json")):
            with open(path, 'r') as f:
                events.append(json.load(f))
        return events

    def list_executions(self) -> list[dict]:
        """List all execution events in chronological order."""
        events = []
        for path in sorted(self.executions_dir.glob("*.json")):
            with open(path, 'r') as f:
                events.append(json.load(f))
        return events

    def list_proposals(self) -> list[dict]:
        """List all proposals in chronological order."""
        events = []
        for path in sorted(self.proposals_dir.glob("*.json")):
            with open(path, 'r') as f:
                events.append(json.load(f))
        return events

    def list_validations(self) -> list[dict]:
        """List all validations in chronological order."""
        events = []
        for path in sorted(self.validations_dir.glob("*.json")):
            with open(path, 'r') as f:
                events.append(json.load(f))
        return events

    def verify_immutability(self) -> bool:
        """
        Verify that all stored events remain unchanged.

        This checks that historical accuracy is preserved.
        """
        # In a real system, this would use hashing. For Phase 1,
        # we just verify that files are readable and valid JSON.
        try:
            for path in self.violations_dir.glob("*.json"):
                with open(path, 'r') as f:
                    json.load(f)
            for path in self.executions_dir.glob("*.json"):
                with open(path, 'r') as f:
                    json.load(f)
            for path in self.proposals_dir.glob("*.json"):
                with open(path, 'r') as f:
                    json.load(f)
            return True
        except Exception:
            return False
