"""A source's tracked items and why skipped ones were set aside, on both clients.

``integrations.tracked_items`` and ``integrations.iter_tracked_items`` (``GET
/v1/projects/{project_id}/sources/{attachment_id}/tracked-items``). The shape is
the engine's ``TrackedItemsPage``.
"""

from __future__ import annotations

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
