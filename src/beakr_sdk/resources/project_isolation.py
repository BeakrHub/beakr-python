"""Project isolation settings: ``client.project_isolation``.

They live under ``/v1/organizations/{org_id}/project-isolation``; when
``org_id`` is omitted the caller's active organization is looked up first.
An organization admin decides how agent sessions keep projects apart, with
project boundaries (each project's own is ``projects.set_boundary``):

- ``session_mode``: ``automatic`` (a session starts open and the server tracks
  what it touches) or ``must_pick`` (every agent session chooses its projects,
  or read everything, before it starts);
- ``default_boundary``: the ``{content_out, content_in}`` new projects start with;
- ``minimum_boundary``: a floor no project goes below (``None`` for none;
  ``{"content_out": "locked"}`` keeps every project locked, what auto-lock was);
- ``agent_writes_require_session``: refuse Knowledge and file writes made with
  an API key or token outside a session (service accounts are exempt).

``available`` says which of these the installation has turned on; a setting
whose feature is off cannot be changed (404 ``feature_not_enabled``). ``presets``
lists the boundary presets (``internal``, ``siloed``, ``leadership``).

Retired by project boundaries, and kept as arguments for one release (each warns,
and the server answers :class:`~beakr_sdk.errors.SettingRetiredError` naming what
to use): ``auto_lock_counts_search_results``, ``cross_project_guard``, and the
session modes ``picker`` and ``auto_lock``.
"""

from __future__ import annotations

import warnings
from typing import Any

from beakr_sdk._calls import Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk.errors import NotProvidedError
from beakr_sdk.resources._base import Resource

# "Not passed": distinct from None, which clears the minimum boundary.
_UNSET: Any = object()


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
        default_boundary: dict[str, str] | None = None,
        minimum_boundary: dict[str, str] | None = _UNSET,
        agent_writes_require_session: bool | None = None,
        auto_lock_counts_search_results: bool | None = None,
        cross_project_guard: str | None = None,
        org_id: str | None = None,
    ) -> Any:
        """Change the settings given (an organization admin acting as a person).

        ``default_boundary`` and ``minimum_boundary`` take ``{content_out,
        content_in}``; ``minimum_boundary=None`` clears the floor, and leaving it
        out keeps it. A default below the minimum raises
        :class:`~beakr_sdk.errors.BoundaryBelowMinimumError`.
        ``auto_lock_counts_search_results`` and ``cross_project_guard`` are retired
        (a ``DeprecationWarning``; the server refuses them).
        """
        if session_mode in {"picker", "auto_lock"}:
            warnings.warn(
                f"session_mode {session_mode!r} was replaced by project boundaries: use "
                "'must_pick' for picker, and 'automatic' with minimum_boundary "
                "{'content_out': 'locked'} for auto_lock; the server refuses it.",
                DeprecationWarning,
                stacklevel=2,
            )
        for name, value in (
            ("auto_lock_counts_search_results", auto_lock_counts_search_results),
            ("cross_project_guard", cross_project_guard),
        ):
            if value is not None:
                warnings.warn(
                    f"{name} was replaced by project boundaries (default_boundary, "
                    "minimum_boundary and each project's boundary); the server refuses it.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        body = clean_body(
            {
                "session_mode": session_mode,
                "default_boundary": default_boundary,
                "agent_writes_require_session": agent_writes_require_session,
                "auto_lock_counts_search_results": auto_lock_counts_search_results,
                "cross_project_guard": cross_project_guard,
            }
        )
        if minimum_boundary is not _UNSET:
            body["minimum_boundary"] = minimum_boundary

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield ops.build_organizations_set_project_isolation(resolved, body=body))  # type: ignore[arg-type]

        return self._drive(flow())

    def read_trail(self, *, org_id: str | None = None) -> Any:
        """How long session reads are kept (``read_trail_retention_days``, 90 by default).

        Also whether the read trail is ``available`` and ``can_change``.
        """

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield ops.build_organizations_get_read_trail_settings(resolved))

        return self._drive(flow())

    def set_read_trail_retention(self, days: int, *, org_id: str | None = None) -> Any:
        """Keep session reads ``days`` days, 1 to 3650 (an organization admin, as a person)."""
        body = {"read_trail_retention_days": days}

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield ops.build_organizations_set_read_trail_settings(resolved, body=body))  # type: ignore[arg-type]

        return self._drive(flow())
