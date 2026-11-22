"""Stopping queued Knowledge work (ENG-2451), on both clients.

``knowledge.compile_queue`` and ``knowledge.cancel_queued_compiles`` (``GET`` and
``POST /v1/projects/{project_id}/knowledge/compiles[/cancel]``), and
``integrations.cancel_source_sync`` (``POST
/v1/projects/{project_id}/sources/{attachment_id}/sync/cancel``). Shapes are the
engine's ``CompileQueueOut``, ``CompileCancelOut`` and ``SyncCancelOut``.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_sdk.errors import ConflictError

from .conftest import BASE

COMPILES = f"{BASE}/v1/projects/p1/knowledge/compiles"
SYNC_CANCEL = f"{BASE}/v1/projects/p1/sources/s1/sync/cancel"
QUEUE = {
    "queued": 12,
    "running": 2,
    "last_cancel": {
        "cancel_id": "c1",
        "status": "cancelling",
        "done": 12,
        "total": 40,
        "summary": "Cancelling: 12 of 40 queued compiles skipped",
        "cancelled_by": "Dana",
        "cancelled_at": "2026-11-21T12:00:00Z",
    },
}
CANCELLED = {
    "cancel_id": "c1",
    "scope": "source",
    "status": "cancelling",
    "will_be_skipped": 40,
    "already_running": 2,
    "sync_stopped": True,
    "syncs_stopped": 1,
    "sync_job_id": "j1",
    "next_sync": "This source does not sync automatically.",
    "note": "Finished work is kept.",
}
SYNC_CANCELLED = {
    "sync_job_id": "j1",
    "status": "cancelling",
    "cancelled_by": "Dana",
    "next_sync": "This source does not sync automatically.",
    "note": "Items already synced are kept.",
}


@respx.mock
def test_compile_queue_reads_the_project_or_one_source(client) -> None:
    route = respx.get(COMPILES).respond(200, json=QUEUE)
    assert client.knowledge.compile_queue("p1") == QUEUE
    assert not route.calls.last.request.url.params
    client.knowledge.compile_queue("p1", attachment_id="s1")
    assert dict(route.calls.last.request.url.params) == {"attachment_id": "s1"}
    client.knowledge.compile_queue("p1", artifact_id="f1")
    assert dict(route.calls.last.request.url.params) == {"artifact_id": "f1"}


@pytest.mark.parametrize(
    ("kwargs", "body"),
    [
        ({}, {}),
        ({"attachment_id": "s1"}, {"attachment_id": "s1"}),
        ({"artifact_id": "f1"}, {"artifact_id": "f1"}),
    ],
    ids=["project", "source", "file"],
)
@respx.mock
def test_cancel_queued_compiles_sends_only_the_source_given(client, kwargs, body) -> None:
    route = respx.post(f"{COMPILES}/cancel").respond(202, json=CANCELLED)
    assert client.knowledge.cancel_queued_compiles("p1", **kwargs) == CANCELLED
    assert json.loads(route.calls.last.request.content) == body
    # A cancel is safe to retry: it carries an idempotency key like every write.
    assert route.calls.last.request.headers.get("Idempotency-Key")


@respx.mock
def test_cancel_source_sync_posts_to_the_source(client) -> None:
    route = respx.post(SYNC_CANCEL).respond(200, json=SYNC_CANCELLED)
    assert client.integrations.cancel_source_sync("p1", "s1") == SYNC_CANCELLED
    assert route.call_count == 1


@respx.mock
def test_cancel_source_sync_with_nothing_running_is_a_conflict(client) -> None:
    respx.post(SYNC_CANCEL).respond(
        409,
        json={"error": {"code": "no_running_sync", "message": "No sync is running."}},
    )
    with pytest.raises(ConflictError) as raised:
        client.integrations.cancel_source_sync("p1", "s1")
    assert raised.value.code == "no_running_sync"


@respx.mock
def test_ids_are_one_encoded_path_segment(client) -> None:
    route = respx.post(f"{BASE}/v1/projects/p%2F1/sources/s%2F1/sync/cancel").respond(
        200, json=SYNC_CANCELLED
    )
    client.integrations.cancel_source_sync("p/1", "s/1")
    assert route.called


@respx.mock
async def test_async_queue_and_cancels(async_client) -> None:
    respx.get(COMPILES).respond(200, json=QUEUE)
    post = respx.post(f"{COMPILES}/cancel").respond(202, json=CANCELLED)
    respx.post(SYNC_CANCEL).respond(200, json=SYNC_CANCELLED)
    assert await async_client.knowledge.compile_queue("p1", attachment_id="s1") == QUEUE
    assert await async_client.knowledge.cancel_queued_compiles("p1") == CANCELLED
    assert json.loads(post.calls.last.request.content) == {}
    assert await async_client.integrations.cancel_source_sync("p1", "s1") == SYNC_CANCELLED
