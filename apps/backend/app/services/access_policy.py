"""Explicit authorization boundary for retrieval.

The current MVP accepts a principal in the request so it can be integration-tested.
A production API gateway must populate this structure from authenticated identity claims,
not from an untrusted browser body.
"""

from dataclasses import dataclass
from uuid import UUID


class AccessDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    user_id: str
    project_ids: set[str]
    roles: set[str]
    all_projects: bool = False


class AccessPolicy:
    def to_principal(self, raw: object) -> Principal:
        return Principal(
            user_id=str(raw.user_id),
            project_ids={str(item) for item in raw.project_ids},
            roles={str(item) for item in raw.roles},
            all_projects=bool(getattr(raw, "all_projects", False)),
        )

    def require_project_access(self, principal: Principal, project_id: str) -> None:
        try:
            UUID(project_id)
        except ValueError as error:
            raise AccessDeniedError("Invalid project identifier") from error
        if not principal.all_projects and project_id not in principal.project_ids:
            raise AccessDeniedError("Principal is not authorized for this project")

    def can_read_scope(self, scope: object, principal: Principal) -> bool:
        if not isinstance(scope, dict):
            return False
        required_roles = scope.get("roles", [])
        if not required_roles:
            return True
        if not isinstance(required_roles, list):
            return False
        return bool(principal.roles.intersection(str(role) for role in required_roles))

    def roles_for_filter(self, principal: Principal) -> list[str]:
        return sorted(principal.roles)
