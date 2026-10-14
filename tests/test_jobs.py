"""``beakr.jobs`` and ``beakr.wait``: any long operation, polled with backoff (W1.2.7)."""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_sdk.resources.jobs import JobFailed, JobTimeout, job_id_of, next_interval

from .conftest import BASE


def _states(*statuses: str) -> list[httpx.Response]:
    return [httpx.Response(200, json={"job_id": "j1", "status": s}) for s in statuses]


@respx.mock
def test_get_reads_one_job(client) -> None:
    respx.get(f"{BASE}/v1/jobs/j1").respond(json={"job_id": "j1", "status": "running"})
    assert client.jobs.get("j1")["status"] == "running"


@respx.mock
def test_wait_backs_off_up_to_its_ceiling(client, sleeps) -> None:
    respx.get(f"{BASE}/v1/jobs/j1").mock(
        side_effect=_states("queued", "running", "running", "running", "succeeded")
    )
    done = client.wait({"job_id": "j1", "status_url": "/v1/jobs/j1"}, interval=1, max_interval=3)
    assert done["status"] == "succeeded"
    assert sleeps == [1, 1.5, 2.25, 3]


@respx.mock
def test_wait_returns_a_failed_job_unless_asked_to_raise(client) -> None:
    respx.get(f"{BASE}/v1/jobs/j1").mock(side_effect=_states("failed", "failed"))
    assert client.jobs.wait("j1")["status"] == "failed"
    with pytest.raises(JobFailed):
        client.jobs.wait("j1", raise_on_failure=True)


@respx.mock
def test_wait_gives_up_with_the_last_state(client) -> None:
    respx.get(f"{BASE}/v1/jobs/j1").respond(json={"job_id": "j1", "status": "running"})
    with pytest.raises(JobTimeout) as raised:
        client.jobs.wait("j1", timeout=0)
    assert raised.value.last["status"] == "running"
    assert raised.value.job_id == "j1"


@respx.mock
async def test_async_wait(async_client, sleeps) -> None:
    respx.get(f"{BASE}/v1/jobs/j1").mock(side_effect=_states("running", "cancelled"))
    done = await async_client.wait("j1", interval=2)
    assert done["status"] == "cancelled"
    assert sleeps == [2]


def test_a_job_reference_is_a_job_id_or_carries_one() -> None:
    assert job_id_of("j1") == "j1"
    assert job_id_of({"run_id": "r", "job_id": "r"}) == "r"
    with pytest.raises(ValueError):
        job_id_of({"run_id": "r"})


def test_bad_backoff_settings_are_refused(client) -> None:
    with pytest.raises(ValueError):
        client.jobs.wait("j1", interval=0)
    assert next_interval(10, backoff=2, max_interval=15) == 15
