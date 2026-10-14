"""Projects: ``/v1/projects``.

Helpers over the generated operations (``beakr.api.projects``): each method
builds its request with the generated, contract-typed builder and adds the
conveniences (whole lists, an archive shortcut).

Example::

    project = beakr.projects.create("Platform", description="Infra and tooling")
    beakr.projects.archive(project["id"])
    beakr.projects.delete(project["id"])   # permanent: there is no trash
"""

from __future__ import annotations

import builtins
from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource


class Projects(Resource[M]):
    @returns_all(ops.build_projects_list_projects)
    def list(self, *, member_only: bool | None = None) -> Any:
        """Every project the caller can see, including their personal project."""
        return self._list_all(ops.build_projects_list_projects(member_only=member_only))

    @returns(ops.build_projects_get_project)
    def get(self, project_id: str) -> Any:
        return self._run(ops.build_projects_get_project(project_id))

    def create(
        self,
        name: str,
        *,
        description: str | None = None,
        tags: builtins.list[str] | None = None,
    ) -> Any:
        body = clean_body({"name": name, "description": description, "tags": tags})
        return self._run(ops.build_projects_create_project(body=body))

    def update(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        tags: builtins.list[str] | None = None,
    ) -> Any:
        body = clean_body(
            {"name": name, "description": description, "status": status, "tags": tags}
        )
        return self._run(ops.build_projects_update_project(project_id, body=body))

    def archive(self, project_id: str) -> Any:
        """Archive (reversible); ``update(status="active")`` restores it."""
        return self.update(project_id, status="archived")

    @returns(ops.build_projects_delete_project)
    def delete(self, project_id: str) -> Any:
        """Delete the project permanently; it cannot be restored (``archive`` is reversible).

        Needs admin access to the project; a personal project cannot be deleted (403).
        """
        return self._run(ops.build_projects_delete_project(project_id))

    @returns_all(ops.build_projects_list_members)
    def members(self, project_id: str) -> Any:
        return self._list_all(ops.build_projects_list_members(project_id))

    def add_member(
        self,
        project_id: str,
        principal_id: str,
        *,
        role: str = "member",
        principal_type: str = "user",
    ) -> Any:
        """Give a user (or group) a role in the project."""
        body = {"principal_id": principal_id, "principal_type": principal_type, "role": role}
        return self._run(ops.build_projects_add_member(project_id, body=body))

    @returns(ops.build_projects_remove_member)
    def remove_member(
        self, project_id: str, principal_id: str, *, principal_type: str = "user"
    ) -> Any:
        return self._run(ops.build_projects_remove_member(project_id, principal_type, principal_id))

    def invite(
        self,
        project_id: str,
        email: str,
        *,
        role: str = "member",
        expires_in_days: int | None = None,
    ) -> Any:
        """Invite someone by email to join the project."""
        body = clean_body(
            {"invitee_email": email, "role": role, "expires_in_days": expires_in_days}
        )
        return self._run(
            ops.build_projects_create_project_membership_invitation(project_id, body=body)
        )

    @returns_all(ops.build_projects_list_project_pending_invitations)
    def pending_invitations(self, project_id: str) -> Any:
        return self._list_all(ops.build_projects_list_project_pending_invitations(project_id))

    @returns(ops.build_projects_revoke_project_membership_invitation)
    def revoke_invitation(self, project_id: str, invitation_id: str) -> Any:
        return self._run(
            ops.build_projects_revoke_project_membership_invitation(project_id, invitation_id)
        )
