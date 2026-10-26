"""Projects: ``/v1/projects``.

Helpers over the generated operations (``beakr.api.projects``): each method
builds its request with the generated, contract-typed builder and adds the
conveniences (whole lists, an archive shortcut).

Example::

    project = beakr.projects.create("Platform", description="Infra and tooling")
    beakr.projects.archive(project["id"])
    beakr.projects.delete(project["id"])   # permanent: there is no trash

Knowledge profile (what Knowledge should understand about the project)::

    beakr.projects.set_knowledge_profile(
        project_id,
        description="Batch records and deviations for the Basel site",
        goals=["Track deviations to closure"],
        entity_types=["batch", "deviation", "capa"],
        core_people=[{"name": "Priya Natarajan", "role": "Batch owner"}],
    )
    beakr.projects.update_knowledge_profile(project_id, entity_types=["batch", "deviation"])
    profile = beakr.projects.knowledge_profile(project_id)

Knowledge readiness::

    status = beakr.projects.wait_until_ready(project_id, timeout=900)
    if status["failed"]:
        for failed in status["failed_files"]:
            print(failed["path"], failed["error_code"], failed["reason"])
        beakr.projects.retry_failed(project_id)
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource
from beakr_sdk.resources.knowledge_readiness import wait_until_ready_flow
from beakr_sdk.resources.model_refusals import ModelRefusals

_PROFILE_FIELDS = frozenset(
    {
        "name",
        "description",
        "industry",
        "goals",
        "focus_areas",
        "core_people",
        "core_organizations",
        "core_topics",
        "entity_types",
        "extraction_guidance",
    }
)


class Projects(Resource[M]):
    def __init__(self, client: Any) -> None:
        super().__init__(client)
        #: When a model refused a Knowledge stage in a project (``model_refusals``).
        self.model_refusals: ModelRefusals[M] = ModelRefusals(client)

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
        While the organization's authentication policy requires step-up (on by
        default) or enterprise SSO, API keys and tokens are refused
        (``session_required``): delete it in the web app, or ``archive`` it here.
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
        role: str | None = None,
        principal_type: str = "user",
    ) -> Any:
        """Give a user, service account or group a role in the project.

        A service account is added by its ``user_id`` with ``principal_type``
        ``"user"`` (the default) or ``"service"``; either way it is listed as a
        ``user`` with ``principal_kind`` ``service_account`` and gets the access
        its role gives. ``"service"`` with an id that is not one of the
        organization's service accounts is refused (422 ``validation_error``).

        Without ``role``, none is sent: a new member joins as ``member`` and an
        existing member keeps the role they have. Demoting the last owner is
        refused (``409 last_owner``).
        """
        body: dict[str, Any] = {"principal_id": principal_id, "principal_type": principal_type}
        if role is not None:
            body["role"] = role
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

    # -- Knowledge readiness -------------------------------------------------

    @returns(ops.build_projects_get_project_knowledge_status)
    def knowledge_status(self, project_id: str, *, failed_limit: int | None = None) -> Any:
        """Whether the project's Knowledge is ready, its coverage, and what failed.

        ``{total, eligible, syncing, synced, knowledge_skipped, oldest_pending_at,
        queued, running, failed, ready, failed_files, failed_files_next_cursor}``.
        ``ready`` is true when nothing is queued or running; ``failed_files`` are
        the newest failures (``failed_limit``, default 20), each with
        ``error_code``, ``reason`` and ``retryable``. Counts cover the files you
        can read.
        """
        return self._run(
            ops.build_projects_get_project_knowledge_status(project_id, failed_limit=failed_limit)
        )

    @returns_all(ops.build_projects_list_knowledge_failed_files)
    def knowledge_failures(self, project_id: str) -> Any:
        """Every file whose latest attempt to reach the project's Knowledge failed, newest first."""
        return self._list_all(ops.build_projects_list_knowledge_failed_files(project_id))

    def wait_until_ready(
        self,
        project_id: str,
        *,
        timeout: float = 600.0,
        poll: float = 5.0,
        max_interval: float = 30.0,
        raise_on_failure: bool = False,
        on_status: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        """Poll :meth:`knowledge_status` until nothing is queued or running; return it.

        Waits ``poll`` seconds between reads, growing to ``max_interval``, and
        hands each status read to ``on_status`` (to show progress).
        Raises :class:`~beakr_sdk.resources.knowledge_readiness.KnowledgeTimeout`
        (with the last status) after ``timeout`` seconds. Failed files do not
        stop the wait; they are in the answer, and with ``raise_on_failure``
        :class:`~beakr_sdk.resources.knowledge_readiness.KnowledgeFailed` is
        raised instead when any remain.
        """
        return self._drive(
            wait_until_ready_flow(
                project_id,
                timeout=timeout,
                poll=poll,
                max_interval=max_interval,
                raise_on_failure=raise_on_failure,
                on_status=on_status,
            )
        )

    @returns(ops.build_projects_retry_failed_knowledge)
    def retry_failed(self, project_id: str, artifact_ids: builtins.list[str] | None = None) -> Any:
        """Send failed files to Knowledge again: every failed file, or ``artifact_ids``.

        Safe to repeat: files already queued or running are left alone. Answers
        ``{queued, already_queued, not_failed, not_found, skipped, files, has_more}``;
        ``files`` says what happened to each file, and ``has_more`` that more
        failed files remain than one call retries (500). Needs permission to add
        files to the project.
        """
        body = None if artifact_ids is None else {"artifact_ids": list(artifact_ids)}
        return self._run(ops.build_projects_retry_failed_knowledge(project_id, body=body))

    # --- Knowledge profile --------------------------------------------------

    @returns(ops.build_projects_get_project_knowledge_profile)
    def knowledge_profile(self, project_id: str) -> Any:
        """The project's Knowledge profile.

        Raises ``NotFoundError`` with ``code == "knowledge_profile_not_found"``
        when the project has none yet (set one with ``set_knowledge_profile``).
        Project members read it; a viewer is refused.
        """
        return self._run(ops.build_projects_get_project_knowledge_profile(project_id))

    def set_knowledge_profile(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        industry: str | None = None,
        goals: builtins.list[str] | None = None,
        focus_areas: builtins.list[str] | None = None,
        core_people: builtins.list[dict[str, Any]] | None = None,
        core_organizations: builtins.list[dict[str, Any]] | None = None,
        core_topics: builtins.list[dict[str, Any]] | None = None,
        entity_types: builtins.list[str] | None = None,
        extraction_guidance: str | None = None,
    ) -> Any:
        """Set the whole Knowledge profile, creating it if the project has none.

        Fields left out are set to their empty value. People are ``{"name", "role",
        "relevance"}``, organizations ``{"name", "type", "relevance"}``, topics
        ``{"name", "relevance"}``. Only the project's owners may set it; Knowledge
        reads it when it next compiles files.
        """
        body = clean_body(
            {
                "name": name,
                "description": description,
                "industry": industry,
                "goals": goals,
                "focus_areas": focus_areas,
                "core_people": core_people,
                "core_organizations": core_organizations,
                "core_topics": core_topics,
                "entity_types": entity_types,
                "extraction_guidance": extraction_guidance,
            }
        )
        return self._run(ops.build_projects_put_project_knowledge_profile(project_id, body=body))

    def update_knowledge_profile(self, project_id: str, **fields: Any) -> Any:
        """Change the fields given; the rest are kept. A list given replaces that list.

        Takes the same fields as ``set_knowledge_profile``. Raises ``NotFoundError``
        (``knowledge_profile_not_found``) when the project has no profile yet.
        """
        unknown = set(fields) - _PROFILE_FIELDS
        if unknown:
            raise TypeError(f"unknown Knowledge profile field(s): {', '.join(sorted(unknown))}")
        return self._run(
            ops.build_projects_patch_project_knowledge_profile(project_id, body=fields)  # type: ignore[arg-type]
        )
