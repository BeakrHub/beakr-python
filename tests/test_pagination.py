"""Every public list pages the same way: ``{data, next_cursor, has_more}`` (spec W1.2, W6.1).

Root cause guarded: list methods read only the first page of a list (``as_list``
over one response), so once the engine paged every list, a caller with more
than one page of projects, members or labels silently saw only the first.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr

BASE = "https://api.test"


def _pages(route: respx.Route) -> None:
    route.side_effect = [
        httpx.Response(200, json={"data": [{"id": "a"}], "next_cursor": "c1", "has_more": True}),
        httpx.Response(200, json={"data": [{"id": "b"}], "next_cursor": None, "has_more": False}),
    ]


@respx.mock
def test_a_list_method_returns_every_page() -> None:
    route = respx.get(f"{BASE}/v1/labels")
    _pages(route)
    with Beakr(api_key="bk_test_x", base_url=BASE) as client:
        assert [label["id"] for label in client.labels.list()] == ["a", "b"]
    first, second = (call.request.url.params for call in route.calls)
    assert first["limit"] == "200" and "cursor" not in first
    assert second["cursor"] == "c1"


@respx.mock
def test_paginate_walks_any_public_list() -> None:
    route = respx.get(f"{BASE}/v1/projects")
    _pages(route)
    with Beakr(api_key="bk_test_x", base_url=BASE) as client:
        ids = [p["id"] for p in client.paginate("/v1/projects", page_size=1, member_only=True)]
    assert ids == ["a", "b"]
    assert route.calls[0].request.url.params["member_only"] == "true"
    assert route.calls[0].request.url.params["limit"] == "1"


@respx.mock
@pytest.mark.asyncio
async def test_the_async_client_pages_the_same_way() -> None:
    _pages(respx.get(f"{BASE}/v1/labels"))
    async with AsyncBeakr(api_key="bk_test_x", base_url=BASE) as client:
        assert [label["id"] for label in await client.labels.list()] == ["a", "b"]
    _pages(respx.get(f"{BASE}/v1/projects"))
    async with AsyncBeakr(api_key="bk_test_x", base_url=BASE) as client:
        assert [p["id"] async for p in client.paginate("/v1/projects")] == ["a", "b"]


@respx.mock
def test_a_list_stops_when_the_server_says_there_is_no_more() -> None:
    route = respx.get(f"{BASE}/v1/labels").respond(
        json={"data": [{"id": "a"}], "next_cursor": "stale", "has_more": False}
    )
    with Beakr(api_key="bk_test_x", base_url=BASE) as client:
        assert client.labels.list() == [{"id": "a"}]
    assert route.call_count == 1
