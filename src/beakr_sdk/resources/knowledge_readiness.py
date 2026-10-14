"""Wait for Knowledge: a project's, or an upload batch's files'.

A file reaches a project's Knowledge in the background after it is indexed.
These flows poll until nothing is queued or running and hand back the last
answer, so a program can tell "ready" from "ready, but some files failed":

- :func:`wait_until_ready_flow` polls ``GET /v1/projects/{project_id}/knowledge-summary``
  (``projects.wait_until_ready``);
- :func:`wait_for_batch_knowledge_flow` polls ``GET /v1/bulk/{batch_id}/status``,
  whose ``knowledge`` counts only that batch's files (``files.upload_many(wait="knowledge")``).

Failed files do not end the wait early: a project can be retrying some while
others still compile. They are in the answer (``failed``, ``failed_files``),
and :class:`KnowledgeFailed` is raised for them only when asked.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from beakr_sdk._calls import Flow, Sleep
from beakr_sdk._generated import operations as ops
from beakr_sdk.resources.jobs import next_interval


class KnowledgeTimeout(TimeoutError):
    """Knowledge was still queued or running when the wait gave up.

    ``last`` is the last answer read (the project status, or the batch status).
    """

    def __init__(self, what: str, last: Mapping[str, Any]) -> None:
        counts = _counts(last)
        super().__init__(
            f"Knowledge for {what} is not ready: "
            f"{counts.get('queued', 0)} queued, {counts.get('running', 0)} running"
        )
        self.last = dict(last)


class KnowledgeFailed(RuntimeError):
    """Knowledge finished with failed files (raised only with ``raise_on_failure``).

    ``status`` is the final answer; for a project its ``failed_files`` say which
    files failed and why, and ``projects.retry_failed`` sends them again.
    """

    def __init__(self, what: str, status: Mapping[str, Any]) -> None:
        failed = _counts(status).get("failed", 0)
        noun = "file" if failed == 1 else "files"
        super().__init__(f"Knowledge for {what} is ready, but {failed} {noun} failed")
        self.status = dict(status)


def _counts(answer: Mapping[str, Any]) -> Mapping[str, Any]:
    """The queued/running/failed counts of a project status or a batch status."""
    knowledge = answer.get("knowledge")
    return knowledge if isinstance(knowledge, Mapping) else answer


def _ready(answer: Mapping[str, Any]) -> bool:
    counts = _counts(answer)
    if "ready" in counts:
        return bool(counts["ready"])
    return not (counts.get("queued") or counts.get("running"))


def _poll(
    read: Any,
    what: str,
    *,
    timeout: float,
    poll: float,
    max_interval: float,
    backoff: float,
    raise_on_failure: bool,
    on_status: Callable[[dict[str, Any]], None] | None,
) -> Flow:
    if poll <= 0 or backoff < 1 or max_interval < poll:
        raise ValueError("Need poll > 0, backoff >= 1 and max_interval >= poll.")
    deadline = time.monotonic() + timeout
    wait_for = poll
    while True:
        answer = yield read
        if on_status is not None:
            on_status(answer)
        if _ready(answer):
            if raise_on_failure and _counts(answer).get("failed"):
                raise KnowledgeFailed(what, answer)
            return answer
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KnowledgeTimeout(what, answer)
        yield Sleep(min(wait_for, remaining))
        wait_for = next_interval(wait_for, backoff=backoff, max_interval=max_interval)


def wait_until_ready_flow(
    project_id: str,
    *,
    timeout: float = 600.0,
    poll: float = 5.0,
    max_interval: float = 30.0,
    backoff: float = 1.5,
    raise_on_failure: bool = False,
    failed_limit: int | None = None,
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> Flow:
    """Poll the project's Knowledge status until nothing is queued or running."""
    return (
        yield from _poll(
            ops.build_projects_get_project_knowledge_status(project_id, failed_limit=failed_limit),
            f"project {project_id}",
            timeout=timeout,
            poll=poll,
            max_interval=max_interval,
            backoff=backoff,
            raise_on_failure=raise_on_failure,
            on_status=on_status,
        )
    )


def wait_for_batch_knowledge_flow(
    batch_id: str,
    *,
    timeout: float = 600.0,
    poll: float = 5.0,
    max_interval: float = 30.0,
    backoff: float = 1.5,
    raise_on_failure: bool = False,
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> Flow:
    """Poll an upload batch's status until none of its files is queued or running for Knowledge."""
    return (
        yield from _poll(
            ops.build_uploads_get_bulk_upload_status(batch_id),
            f"batch {batch_id}",
            timeout=timeout,
            poll=poll,
            max_interval=max_interval,
            backoff=backoff,
            raise_on_failure=raise_on_failure,
            on_status=on_status,
        )
    )
