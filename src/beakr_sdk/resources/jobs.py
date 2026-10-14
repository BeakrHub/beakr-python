"""Long operations as jobs: read one, or wait for it (``GET /v1/jobs/{job_id}``).

Every operation that starts background work -- an agent or workflow run, a
project import, an integration sync, a webpage import, a Knowledge compile, a
bulk upload -- answers with ``job_id`` and ``status_url``. A job reads the same
way whatever its kind::

    {"job_id", "kind", "status", "native_status", "progress", "result",
     "error", "project_id", "created_at", "finished_at", "status_url",
     "resource_url"}

``status`` is one of ``queued``, ``running``, ``succeeded``, ``failed`` or
``cancelled``; the last three are final. Reading a job needs a scope of its
kind -- the scope of the operation that started it is always enough.

Example::

    run = beakr.runs.start("Summarize this week's assay results")
    job = beakr.wait(run)              # or beakr.jobs.wait(run["job_id"])
    if job["status"] == "succeeded":
        print(job["result"])
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from beakr_sdk._calls import Flow, Sleep
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource

#: Job states that will not change again.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class JobTimeout(TimeoutError):
    """The job was still running when :meth:`Jobs.wait` gave up."""

    def __init__(self, job_id: str, last: Mapping[str, Any]) -> None:
        super().__init__(f"Job {job_id} is still {last.get('status')!r}")
        self.job_id = job_id
        self.last = dict(last)


class JobFailed(RuntimeError):
    """The job finished ``failed`` or ``cancelled`` (raised only with ``raise_on_failure``)."""

    def __init__(self, job: Mapping[str, Any]) -> None:
        error = job.get("error") or {}
        detail = error.get("message") or error.get("code") or job.get("status")
        super().__init__(f"Job {job.get('job_id')} {job.get('status')}: {detail}")
        self.job = dict(job)


def job_id_of(job: str | Mapping[str, Any]) -> str:
    """The id from a job id, a job, or any response that started one.

    Accepts ``{"job_id": ...}`` (every job-starting response and every job) or
    a bare id.
    """
    if isinstance(job, str):
        return job
    value = job.get("job_id")
    if not value:
        raise ValueError("This response does not reference a job (no job_id).")
    return str(value)


def next_interval(current: float, *, backoff: float, max_interval: float) -> float:
    """The next poll interval: ``current * backoff``, capped at ``max_interval``."""
    return min(max_interval, current * backoff)


class Jobs(Resource[M]):
    @returns(ops.build_jobs_get_job)
    def get(self, job: str | Mapping[str, Any]) -> Any:
        """The job's current state."""
        return self._run(ops.build_jobs_get_job(job_id_of(job)))

    def wait(
        self,
        job: str | Mapping[str, Any],
        *,
        timeout: float = 600.0,
        interval: float = 1.0,
        max_interval: float = 15.0,
        backoff: float = 1.5,
        raise_on_failure: bool = False,
    ) -> Any:
        """Poll until the job is final; return its last state.

        Polls after ``interval`` seconds, then each wait ``backoff`` times longer,
        never longer than ``max_interval``. Raises :class:`JobTimeout` (with the
        last state) after ``timeout`` seconds, and :class:`JobFailed` for a
        ``failed`` or ``cancelled`` job when ``raise_on_failure`` is set.
        """
        return self._drive(
            self.wait_flow(
                job,
                timeout=timeout,
                interval=interval,
                max_interval=max_interval,
                backoff=backoff,
                raise_on_failure=raise_on_failure,
            )
        )

    def wait_flow(
        self,
        job: str | Mapping[str, Any],
        *,
        timeout: float = 600.0,
        interval: float = 1.0,
        max_interval: float = 15.0,
        backoff: float = 1.5,
        raise_on_failure: bool = False,
    ) -> Flow:
        """:meth:`wait` as a flow, for resources that wait as one step of their own."""
        if interval <= 0 or backoff < 1 or max_interval < interval:
            raise ValueError("Need interval > 0, backoff >= 1 and max_interval >= interval.")
        job_id = job_id_of(job)
        deadline = time.monotonic() + timeout
        wait_for = interval
        while True:
            state = yield ops.build_jobs_get_job(job_id)
            if state.get("status") in TERMINAL_STATUSES:
                if raise_on_failure and state.get("status") != "succeeded":
                    raise JobFailed(state)
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JobTimeout(job_id, state)
            yield Sleep(min(wait_for, remaining))
            wait_for = next_interval(wait_for, backoff=backoff, max_interval=max_interval)
