"""Knowing when Knowledge is ready, what failed, and retrying it.

``projects.knowledge_status`` / ``wait_until_ready`` / ``retry_failed`` /
``knowledge_failures`` and ``files.upload_many(wait="knowledge")``, on both
clients. A wait polls until nothing is queued or running, keeps failed files
in its answer, and raises for them only when asked.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_sdk.resources.knowledge_readiness import KnowledgeFailed, KnowledgeTimeout

from .conftest import BASE

STATUS_URL = f"{BASE}/v1/projects/p1/knowledge-summary"
FAILED_FILE = {
    "artifact_id": "a1",
    "filename": "memo.pdf",
    "path": "deals/memo.pdf",
    "error_code": "processing_failed",
    "reason": "The file could not be read, so nothing from it was added to Knowledge.",
    "failed_at": "2026-10-14T12:00:00Z",
    "retryable": True,
}


def _status(queued: int = 0, running: int = 0, failed: int = 0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "total": 3,
            "eligible": failed,
            "syncing": queued + running,
            "synced": 3 - failed - queued - running,
            "knowledge_skipped": 0,
            "oldest_pending_at": None,
            "queued": queued,
            "running": running,
            "failed": failed,
            "ready": queued + running == 0,
            "failed_files": [FAILED_FILE] * failed,
            "failed_files_next_cursor": None,
        },
    )


@respx.mock
def test_knowledge_status_reads_the_project_summary(client) -> None:
    route = respx.get(STATUS_URL).mock(return_value=_status(running=1, failed=1))
    status = client.projects.knowledge_status("p1", failed_limit=5)
    assert (status["ready"], status["failed"]) == (False, 1)
    assert route.calls.last.request.url.params["failed_limit"] == "5"


@respx.mock
def test_wait_until_ready_polls_until_nothing_is_in_flight(client, sleeps) -> None:
    respx.get(STATUS_URL).mock(
        side_effect=[_status(queued=2), _status(running=1), _status(failed=1)]
    )
    status = client.projects.wait_until_ready("p1", poll=2, max_interval=10)
    assert status["ready"] is True
    assert status["failed_files"][0]["error_code"] == "processing_failed"
    assert sleeps == [2, 3.0]


@respx.mock
def test_wait_until_ready_raises_for_failures_only_when_asked(client) -> None:
    respx.get(STATUS_URL).mock(return_value=_status(failed=2))
    assert client.projects.wait_until_ready("p1")["failed"] == 2
    with pytest.raises(KnowledgeFailed) as raised:
        client.projects.wait_until_ready("p1", raise_on_failure=True)
    assert raised.value.status["failed"] == 2
    assert "2 files failed" in str(raised.value)


@respx.mock
def test_wait_until_ready_gives_up_with_the_last_status(client) -> None:
    respx.get(STATUS_URL).mock(return_value=_status(queued=4))
    with pytest.raises(KnowledgeTimeout) as raised:
        client.projects.wait_until_ready("p1", timeout=0)
    assert raised.value.last["queued"] == 4
    assert "4 queued" in str(raised.value)


@respx.mock
def test_retry_failed_sends_all_or_the_named_files(client) -> None:
    route = respx.post(f"{BASE}/v1/projects/p1/knowledge/retry-failed").respond(
        200, json={"queued": 1, "files": [{"artifact_id": "a1", "outcome": "queued"}]}
    )
    client.projects.retry_failed("p1")
    assert route.calls.last.request.content in (b"", b"{}")
    client.projects.retry_failed("p1", ["a1", "a2"])
    assert json.loads(route.calls.last.request.content) == {"artifact_ids": ["a1", "a2"]}


@respx.mock
def test_knowledge_failures_follows_every_page(client) -> None:
    route = respx.get(f"{BASE}/v1/projects/p1/knowledge/failed-files").mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [FAILED_FILE], "next_cursor": "c2", "has_more": True}
            ),
            httpx.Response(
                200, json={"data": [FAILED_FILE], "next_cursor": None, "has_more": False}
            ),
        ]
    )
    assert len(client.projects.knowledge_failures("p1")) == 2
    assert route.calls.last.request.url.params["cursor"] == "c2"


@respx.mock
async def test_async_wait_until_ready(async_client, sleeps) -> None:
    respx.get(STATUS_URL).mock(side_effect=[_status(running=1), _status()])
    status = await async_client.projects.wait_until_ready("p1", poll=1)
    assert status["ready"] is True
    assert sleeps == [1]


def _batch(knowledge: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "batch_id": "b1",
            "status": "completed",
            "artifacts": [{"id": "a1", "path": "a.md", "title": "a.md", "status": "indexed"}],
            "summary": {"total": 1, "indexed": 1},
            "knowledge": knowledge,
        },
    )


def _mock_upload() -> None:
    respx.post(f"{BASE}/v1/bulk/prepare").respond(
        200,
        json={
            "batch_id": "b1",
            "uploads": [
                {"path": "a.md", "key": "k/a.md", "url": "https://s3.test/a.md", "headers": {}}
            ],
        },
    )
    respx.put(url__startswith="https://s3.test").respond(200)
    respx.post(f"{BASE}/v1/bulk/complete").respond(
        200,
        json={
            "batch_id": "b1",
            "artifacts": [{"id": "a1"}],
            "folders": [],
            "failed": [],
            "job_id": "j1",
            "status_url": "/v1/jobs/j1",
        },
    )
    respx.get(f"{BASE}/v1/jobs/j1").respond(200, json={"job_id": "j1", "status": "succeeded"})


@respx.mock
def test_upload_many_can_wait_for_its_files_knowledge(client, tmp_path, sleeps) -> None:
    (tmp_path / "a.md").write_text("a")
    _mock_upload()
    status = respx.get(f"{BASE}/v1/bulk/b1/status").mock(
        side_effect=[
            _batch({"queued": 1, "running": 0, "failed": 0, "ready": False}),
            _batch({"queued": 0, "running": 0, "failed": 1, "ready": True}),
        ]
    )
    result = client.files.upload_many(tmp_path / "a.md", project_id="p1", wait="knowledge", poll=1)
    assert result["job"]["status"] == "succeeded"
    assert result["batch"]["knowledge"]["failed"] == 1
    assert status.call_count == 2
    assert sleeps == [1]


@respx.mock
async def test_async_upload_many_waits_for_knowledge(async_client, tmp_path) -> None:
    (tmp_path / "a.md").write_text("a")
    _mock_upload()
    respx.get(f"{BASE}/v1/bulk/b1/status").mock(
        return_value=_batch({"queued": 0, "running": 0, "failed": 0, "ready": True})
    )
    result = await async_client.files.upload_many(
        tmp_path / "a.md", project_id="p1", wait="knowledge"
    )
    assert result["batch"]["knowledge"]["ready"] is True


def test_upload_many_refuses_a_knowledge_wait_without_knowledge(client, tmp_path) -> None:
    (tmp_path / "a.md").write_text("a")
    with pytest.raises(ValueError, match="ingest=True"):
        client.files.upload_many(tmp_path / "a.md", project_id="p1", ingest=False, wait="knowledge")
    with pytest.raises(ValueError, match="wait must be"):
        client.files.upload_many(tmp_path / "a.md", project_id="p1", wait="everything")


@respx.mock
def test_wait_until_ready_reports_each_status_it_reads(client) -> None:
    respx.get(STATUS_URL).mock(side_effect=[_status(queued=1), _status()])
    seen: list[int] = []
    client.projects.wait_until_ready("p1", on_status=lambda s: seen.append(s["queued"]))
    assert seen == [1, 0]
