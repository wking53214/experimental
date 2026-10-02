"""
Versioned adaptive boundaries.

A boundary is a resource constraint that can be adjusted by the adaptive mechanism.
Each change creates a new version. Historical versions are never rewritten.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time


class BoundaryStatus(Enum):
    """Status of a boundary version."""
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISABLED = "disabled"


@dataclass
class BoundaryVersion:
    """A single version of a boundary."""
    boundary_id: str
    version: int
    resource_or_action: str
    current_limit: Any
    status: BoundaryStatus
    created_at: float
    parent_version: Optional[int] = None

    def __hash__(self) -> int:
        return hash((self.boundary_id, self.version))


@dataclass
class BoundaryHistory:
    """Complete history of a boundary's versions. Immutable record."""
    boundary_id: str
    versions: dict[int, BoundaryVersion] = field(default_factory=dict)
    _active_version: Optional[int] = None

    def add_version(self, version: BoundaryVersion) -> None:
        """Add a new version. Cannot replace existing versions."""
        if version.version in self.versions:
            raise ValueError(
                f"Version {version.version} of boundary {self.boundary_id} "
                f"already exists. Historical versions are immutable."
            )
        self.versions[version.version] = version
        if version.status == BoundaryStatus.ACTIVE:
            self._active_version = version.version

    def get_version(self, version: int) -> BoundaryVersion:
        """Retrieve a specific version. Never modified after creation."""
        if version not in self.versions:
            raise KeyError(
                f"Version {version} of boundary {self.boundary_id} not found"
            )
        return self.versions[version]

    def get_active_version(self) -> BoundaryVersion:
        """Get the currently active version."""
        if self._active_version is None:
            raise ValueError(
                f"No active version for boundary {self.boundary_id}"
            )
        return self.versions[self._active_version]

    def get_version_history(self) -> list[BoundaryVersion]:
        """Get all versions in order. Immutable historical record."""
        return [self.versions[v] for v in sorted(self.versions.keys())]


class BoundaryStore:
    """
    Manages boundaries and their versions.

    Key invariant: Once a version is recorded, it can never be modified or deleted.
    """

    def __init__(self):
        self.boundaries: dict[str, BoundaryHistory] = {}

    def create_boundary(
        self,
        boundary_id: str,
        resource_or_action: str,
        initial_limit: Any,
    ) -> BoundaryVersion:
        """Create a new boundary with initial limit."""
        if boundary_id in self.boundaries:
            raise ValueError(f"Boundary {boundary_id} already exists")

        version = BoundaryVersion(
            boundary_id=boundary_id,
            version=1,
            resource_or_action=resource_or_action,
            current_limit=initial_limit,
            status=BoundaryStatus.ACTIVE,
            created_at=time.time(),
            parent_version=None,
        )

        history = BoundaryHistory(boundary_id=boundary_id)
        history.add_version(version)
        self.boundaries[boundary_id] = history

        return version

    def update_boundary(
        self,
        boundary_id: str,
        new_limit: Any,
        reason: str = "",
    ) -> BoundaryVersion:
        """
        Update a boundary by creating a new version.
        The previous version becomes superseded.
        """
        if boundary_id not in self.boundaries:
            raise KeyError(f"Boundary {boundary_id} not found")

        history = self.boundaries[boundary_id]
        current = history.get_active_version()

        # Mark previous version as superseded
        old_version = BoundaryVersion(
            boundary_id=current.boundary_id,
            version=current.version,
            resource_or_action=current.resource_or_action,
            current_limit=current.current_limit,
            status=BoundaryStatus.SUPERSEDED,
            created_at=current.created_at,
            parent_version=current.parent_version,
        )
        history.versions[current.version] = old_version

        # Create new active version
        new_version = BoundaryVersion(
            boundary_id=boundary_id,
            version=current.version + 1,
            resource_or_action=current.resource_or_action,
            current_limit=new_limit,
            status=BoundaryStatus.ACTIVE,
            created_at=time.time(),
            parent_version=current.version,
        )
        history.add_version(new_version)

        return new_version

    def get_boundary(self, boundary_id: str) -> BoundaryVersion:
        """Get the currently active version of a boundary."""
        if boundary_id not in self.boundaries:
            raise KeyError(f"Boundary {boundary_id} not found")
        return self.boundaries[boundary_id].get_active_version()

    def get_boundary_history(self, boundary_id: str) -> BoundaryHistory:
        """Get the complete history of a boundary."""
        if boundary_id not in self.boundaries:
            raise KeyError(f"Boundary {boundary_id} not found")
        return self.boundaries[boundary_id]

    def list_boundaries(self) -> list[BoundaryVersion]:
        """List all currently active boundaries."""
        return [h.get_active_version() for h in self.boundaries.values()]
