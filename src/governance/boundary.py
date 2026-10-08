"""
Versioned adaptive boundaries.

A boundary is a resource constraint that can be adjusted by the adaptive mechanism.
Each change creates a new version. Historical versions are never rewritten.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time

from .grant import AuthorizationGrant, is_loosening


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
        # (boundary_id, version) of every version created by an authorized loosening
        self.authorized_loosenings: dict[tuple, str] = {}
        # Called as on_event("boundary_version", payload) BEFORE a new version takes effect. If it
        # raises, the change does not happen (fail closed). Used to make the audit log durable.
        self.on_event = None

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

        if self.on_event:
            self.on_event("boundary_version", {
                "boundary_id": boundary_id, "version": 1, "resource": resource_or_action,
                "limit": initial_limit, "grant_id": None})
        history = BoundaryHistory(boundary_id=boundary_id)
        history.add_version(version)
        self.boundaries[boundary_id] = history

        return version

    def update_boundary(
        self,
        boundary_id: str,
        new_limit: Any,
        reason: str = "",
        grant: Optional[AuthorizationGrant] = None,
    ) -> BoundaryVersion:
        """
        Update a boundary by creating a new version.
        The previous version becomes superseded.

        Raising the limit (anything not provably a tightening) requires a grant issued
        by the AuthorityModel for exactly this boundary and value. The check is made
        against the boundary's actual current limit, not against anything the caller
        declares.
        """
        if boundary_id not in self.boundaries:
            raise KeyError(f"Boundary {boundary_id} not found")

        history = self.boundaries[boundary_id]
        current = history.get_active_version()

        loosening = is_loosening(current.current_limit, new_limit)
        if loosening:
            if grant is None:
                raise PermissionError(
                    f"Refusing to loosen {boundary_id} from {current.current_limit} to "
                    f"{new_limit} without an authorization grant")
            grant.consume(boundary_id, new_limit)

        if self.on_event:
            self.on_event("boundary_version", {
                "boundary_id": boundary_id, "version": current.version + 1,
                "resource": current.resource_or_action, "limit": new_limit,
                "grant_id": grant.grant_id if loosening else None})

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
        if loosening:
            self.authorized_loosenings[(boundary_id, new_version.version)] = grant.grant_id

        return new_version

    def restore_version(self, boundary_id: str, version: int, resource: str, limit: Any,
                        grant_id: Optional[str], created_at: float) -> BoundaryVersion:
        """Rebuild history from a verified log. Not a way to change a limit: versions must arrive in
        order, and a step that raises the limit without a recorded grant is refused."""
        history = self.boundaries.get(boundary_id)
        if version == 1:
            if history is not None:
                raise ValueError(f"boundary {boundary_id} restored twice")
            history = BoundaryHistory(boundary_id=boundary_id)
            self.boundaries[boundary_id] = history
            parent = None
        else:
            if history is None or history.get_active_version().version != version - 1:
                raise ValueError(f"boundary {boundary_id}: version {version} out of order")
            current = history.get_active_version()
            if is_loosening(current.current_limit, limit):
                if not grant_id:
                    raise PermissionError(
                        f"boundary {boundary_id} v{version} raises the limit with no grant on record")
                self.authorized_loosenings[(boundary_id, version)] = grant_id
            history.versions[current.version] = BoundaryVersion(
                boundary_id=boundary_id, version=current.version, resource_or_action=current.resource_or_action,
                current_limit=current.current_limit, status=BoundaryStatus.SUPERSEDED,
                created_at=current.created_at, parent_version=current.parent_version)
            parent = current.version
        v = BoundaryVersion(boundary_id=boundary_id, version=version, resource_or_action=resource,
                            current_limit=limit, status=BoundaryStatus.ACTIVE,
                            created_at=created_at, parent_version=parent)
        history.add_version(v)
        return v

    def unauthorized_loosenings(self) -> list:
        """Audit the version history itself: every step that raises a limit must have a
        recorded grant. This reads the stored versions, not any log kept by the caller,
        so it also catches changes that bypassed update_boundary."""
        found = []
        for bid, history in self.boundaries.items():
            versions = history.get_version_history()
            for prev, cur in zip(versions, versions[1:]):
                if is_loosening(prev.current_limit, cur.current_limit) and \
                        (bid, cur.version) not in self.authorized_loosenings:
                    found.append({"boundary_id": bid, "version": cur.version,
                                  "from": prev.current_limit, "to": cur.current_limit})
        return found

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
