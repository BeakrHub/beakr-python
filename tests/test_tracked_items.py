"""A source's tracked items and why skipped ones were set aside, on both clients.

``integrations.tracked_items`` and ``integrations.iter_tracked_items`` (``GET
/v1/projects/{project_id}/sources/{attachment_id}/tracked-items``). The shape is
the engine's ``TrackedItemsPage``.
"""

from __future__ import annotations

import json

import pytest
import respx

from .conftest import BASE

TRACKED = f"{BASE}/v1/projects/p1/sources/s1/tracked-items"
COUNTS = {"total": 3, "pending": 0, "ingested": 2, "skipped": 1, "removed": 0}
LUNCH = {
    "external_id": "conn:t1",
    "title": "Lunch order",
    "kind": "thread",
    "state": "skipped",
    "reason_code": "nothing_to_add",
    "reason": "A lunch order; nothing this project records.",
}
PROTOCOL = {**LUNCH, "external_id": "conn:t2", "title": "Protocol", "state": "ingested"}


def _page(rows: list[dict], cursor: str | None = None) -> dict:
    return {
        "data": rows,
        "next_cursor": cursor,
        "has_more": cursor is not None,
        "provider": "gmail",
        "kind": "thread",
        "counts": COUNTS,
        "matched": len(rows),
    }


@respx.mock
def test_one_page_with_its_filters(client) -> None:
    route = respx.get(TRACKED).respond(200, json=_page([LUNCH]))

    page = client.integrations.tracked_items(
        "p1", "s1", state="skipped", reason_code="nothing_to_add", search="lunch", limit=10
    )

    assert page["data"][0]["reason_code"] == "nothing_to_add"
    assert page["counts"] == COUNTS
    assert dict(route.calls.last.request.url.params) == {
        "state": "skipped",
        "reason_code": "nothing_to_add",
        "search": "lunch",
        "limit": "10",
    }


@respx.mock
def test_iterating_follows_the_cursor_and_keeps_the_filters(client) -> None:
    route = respx.get(TRACKED).mock(
        side_effect=[
            respx.MockResponse(200, json=_page([LUNCH], cursor="c2")),
            respx.MockResponse(200, json=_page([PROTOCOL])),
        ]
    )

    items = list(
        client.integrations.iter_tracked_items(
            "p1", "s1", since="2026-11-23T00:00:00+00:00", page_size=1
        )
    )

    assert [item["external_id"] for item in items] == ["conn:t1", "conn:t2"]
    second = dict(route.calls.last.request.url.params)
    assert second["cursor"] == "c2"
    assert second["since"] == "2026-11-23T00:00:00+00:00"


@respx.mock
def test_ids_are_one_encoded_path_segment(client) -> None:
    route = respx.get(f"{BASE}/v1/projects/p%2F1/sources/s%2F1/tracked-items").respond(
        200, json=_page([])
    )
    client.integrations.tracked_items("p/1", "s/1")
    assert route.called


@respx.mock
async def test_async_page(async_client) -> None:
    respx.get(TRACKED).respond(200, json=_page([LUNCH]))
    page = await async_client.integrations.tracked_items("p1", "s1", state="skipped")
    assert page["data"] == [LUNCH]


FAILED = {
    **LUNCH,
    "external_id": "conn:t3",
    "title": "Assay plan",
    "state": "failed",
    "reason_code": "compile_failed",
    "reason": "The compile did not finish; the next sync sends it again.",
}


@respx.mock
def test_failed_items_are_filtered_by_their_state(client) -> None:
    route = respx.get(TRACKED).respond(200, json=_page([FAILED]))
    page = client.integrations.tracked_items("p1", "s1", state="failed")
    assert page["data"][0]["reason_code"] == "compile_failed"
    assert dict(route.calls.last.request.url.params) == {"state": "failed"}


HISTORY = f"{BASE}/v1/projects/p1/sources/s1/history-import/status"
ENDED = {
    "provider": "gdrive:nango",
    "supported": True,
    "status": "idle",
    "request": None,
    "progress": None,
    "outcome": None,
    "last_import": {"finished_at": "2026-11-24T03:00:00Z"},
    "last_import_cancelled": False,
    "last_outcome": {
        "in_knowledge": 492,
        "skipped": 93,
        "failed": 26,
        "left": 0,
        "state": "finished",
        "summary": (
            "Import finished on 24 Nov 2026: 492 in Knowledge, 93 skipped, 26 failed, 0 left"
        ),
    },
}


@respx.mock
def test_history_import_status_says_how_the_last_import_ended(client) -> None:
    respx.get(HISTORY).respond(200, json=ENDED)
    status = client.integrations.history_import_status("p1", "s1")
    assert status["last_outcome"]["failed"] == 26
    assert status["last_outcome"]["summary"].startswith("Import finished on 24 Nov 2026")


@respx.mock
async def test_history_import_status_on_the_async_client(async_client) -> None:
    respx.get(HISTORY).respond(200, json=ENDED)
    status = await async_client.integrations.history_import_status("p1", "s1")
    assert status["status"] == "idle"


RECOMPILE = f"{BASE}/v1/projects/p1/sources/s1/recompile"
SENT = {
    "mode": "full",
    "dry_run": False,
    "requested": 2,
    "in_knowledge_left": 0,
    "over_cap": 0,
    "not_found": [],
    "sync": "started",
    "sync_job_id": "11111111-2222-3333-4444-555555555555",
}


@respx.mock
def test_recompile_sends_the_named_items_in_the_mode_asked(client) -> None:
    route = respx.post(RECOMPILE).respond(200, json=SENT)
    answer = client.integrations.recompile(
        "p1", "s1", external_ids=["conn:t1", "conn:t2"], mode="full"
    )
    assert answer["sync"] == "started"
    assert json.loads(route.calls.last.request.content) == {
        "mode": "full",
        "dry_run": False,
        "external_ids": ["conn:t1", "conn:t2"],
    }


@respx.mock
async def test_recompile_every_item_as_a_dry_run_on_the_async_client(async_client) -> None:
    route = respx.post(RECOMPILE).respond(
        200, json={**SENT, "dry_run": True, "sync": "not_started"}
    )
    answer = await async_client.integrations.recompile("p1", "s1", all_items=True, dry_run=True)
    assert answer["dry_run"] is True
    assert json.loads(route.calls.last.request.content) == {
        "mode": "retry",
        "dry_run": True,
        "all": True,
    }


def test_recompile_needs_either_items_or_all(client) -> None:
    with pytest.raises(ValueError):
        client.integrations.recompile("p1", "s1")
    with pytest.raises(ValueError):
        client.integrations.recompile("p1", "s1", external_ids=["conn:t1"], all_items=True)


PARTIAL = {
    **LUNCH,
    "external_id": "conn:t4",
    "title": "Design review",
    "state": "partial",
    "reason_code": "compile_partial",
    "unfinished_changes": 3,
}


@respx.mock
def test_partial_items_are_filtered_by_their_state(client) -> None:
    route = respx.get(TRACKED).respond(200, json=_page([PARTIAL]))
    page = client.integrations.tracked_items("p1", "s1", state="partial")
    assert page["data"][0]["unfinished_changes"] == 3
    assert dict(route.calls.last.request.url.params) == {"state": "partial"}
