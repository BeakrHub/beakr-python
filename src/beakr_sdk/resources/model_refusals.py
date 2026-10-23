"""Model refusals: when a model refused a Knowledge stage, and what happened next.

``GET /v1/projects/{project_id}/model-refusals`` (the events, newest first),
``.../summary`` (counts for a window) and ``.../files`` (per-file counts). A
refusal is a model declining a stage's request; a fallback is the stage moving
to the next model in its ladder; each event's ``outcome`` says whether the work
``recovered``, ``failed`` or was ``skipped``. A file's ``model_refusal_count``
and ``compile_needed_fallback`` (``files.get``, ``files.list``) say the same per
file.

Example::

    summary = beakr.projects.model_refusals.summary(project_id, window="7d")
    for row in beakr.projects.model_refusals.files(project_id)["data"]:
        print(row["title"], row["refusals"], row["fallbacks"])
    events = beakr.projects.model_refusals.list(project_id, outcome="failed")
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource


class ModelRefusals(Resource[M]):
    """``/v1/projects/{project_id}/model-refusals``."""

    @returns(ops.build_projects_list_model_refusals)
    def list(
        self,
        project_id: str,
        *,
        artifact_id: str | None = None,
        outcome: str | None = None,
        stage: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """One page of refusal events, newest first: ``{data, next_cursor, has_more}``.

        ``outcome`` is ``recovered``, ``failed`` or ``skipped``.
        """
        return self._run(
            ops.build_projects_list_model_refusals(
                project_id,
                artifact_id=artifact_id,
                outcome=outcome,  # type: ignore[arg-type]
                stage=stage,
                cursor=cursor,
                limit=limit,
            )
        )

    @returns(ops.build_projects_get_model_refusal_summary)
    def summary(self, project_id: str, window: str | None = None) -> Any:
        """Refusal, fallback and outcome counts for ``window`` (``24h``, ``7d`` or ``30d``)."""
        return self._run(
            ops.build_projects_get_model_refusal_summary(
                project_id,
                window=window,  # type: ignore[arg-type]
            )
        )

    @returns(ops.build_projects_list_model_refusal_files)
    def files(
        self,
        project_id: str,
        *,
        window: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """One page of per-file counts for ``window``: ``{data, next_cursor, has_more}``."""
        return self._run(
            ops.build_projects_list_model_refusal_files(
                project_id,
                window=window,  # type: ignore[arg-type]
                cursor=cursor,
                limit=limit,
            )
        )
