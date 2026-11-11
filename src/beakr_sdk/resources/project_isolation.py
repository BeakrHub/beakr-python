"""Project isolation settings: ``client.project_isolation``.

They live under ``/v1/organizations/{org_id}/project-isolation``; when
``org_id`` is omitted the caller's active organization is looked up first.
An organization admin decides how agent sessions keep projects apart:

- ``session_mode``: ``picker`` (the person chooses each session's projects) or
  ``auto_lock`` (the first project a session reads or writes becomes its only
  write project; reading a second makes it read-only);
- ``auto_lock_counts_search_results``: whether the projects an unscoped
  search's results come from count as read under auto-lock;
- ``agent_writes_require_session``: refuse Knowledge and file writes made with
  an API key or token outside a session (service accounts are exempt);
- ``cross_project_guard``: the default for a write that brings content from
  another project into a project (``allow``, ``hold`` for an owner or admin, or
  ``refuse``); a project's own guard is ``projects.cross_project_guard``.

``available`` says which of these the installation has turned on; a setting
whose feature is off cannot be changed (404 ``feature_not_enabled``).
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk.errors import NotProvidedError
from beakr_sdk.resources._base import Resource


class ProjectIsolation(Resource[M]):
    """``/v1/organizations/{org_id}/project-isolation``."""

    def _org_id_flow(self, org_id: str | None) -> Flow:
        if org_id:
            return org_id
        try:
            org = yield ops.build_organizations_get_active_org()
        except NotProvidedError as exc:
            raise exc.naming("project isolation") from None
        return str(org["id"])

    def get(self, *, org_id: str | None = None) -> Any:
        """The organization's settings, which features are available, and ``can_change``."""

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield ops.build_organizations_get_project_isolation(resolved))

        return self._drive(flow())

    def update(
        self,
        *,
        session_mode: str | None = None,
        auto_lock_counts_search_results: bool | None = None,
        agent_writes_require_session: bool | None = None,
        cross_project_guard: str | None = None,
        org_id: str | None = None,
    ) -> Any:
        """Change the settings given (an organization admin acting as a person)."""
        body = clean_body(
            {
                "session_mode": session_mode,
                "auto_lock_counts_search_results": auto_lock_counts_search_results,
                "agent_writes_require_session": agent_writes_require_session,
                "cross_project_guard": cross_project_guard,
            }
        )

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield ops.build_organizations_set_project_isolation(resolved, body=body))  # type: ignore[arg-type]

        return self._drive(flow())
