"""``Beakr.request``: any public operation through the SDK's own transport.

It is what ``beakr api`` sends every generated command through, so it must
behave like the typed resources: credential and ``Idempotency-Key`` on writes,
no credential for operations the contract marks anonymous, refusal of
non-public paths, and events from a stream.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr

BASE = "https://api.test"


@pytest.fixture
def beakr(isolated_home):
    with Beakr(base_url=BASE, api_key="bk_live_x") as client:
        yield client


@respx.mock
def test_a_write_carries_the_credential_and_an_idempotency_key(beakr):
    route = respx.post(f"{BASE}/v1/projects").respond(201, json={"id": "p1"})
    assert beakr.request("POST", "/v1/projects", json={"name": "A"}) == {"id": "p1"}
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer bk_live_x"
    assert request.headers["idempotency-key"]


@respx.mock
def test_an_anonymous_operation_sends_no_credential(beakr):
    route = respx.get(f"{BASE}/v1/auth/config").respond(200, json={})
    beakr.request("GET", "/v1/auth/config", authenticated=False)
    assert "authorization" not in route.calls[0].request.headers


def test_only_public_paths_are_accepted(beakr):
    with pytest.raises(ValueError):
        beakr.request("GET", "https://elsewhere.test/v1/projects")


@respx.mock
def test_an_event_stream_yields_events(beakr):
    respx.get(f"{BASE}/v1/events/stream").mock(
        return_value=httpx.Response(
            200,
            content=b'id: 1\ndata: {"type": "a"}\n\nid: 2\ndata: {"type": "b"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )
    events = list(beakr.request("GET", "/v1/events/stream", response="sse"))
    assert [e["type"] for e in events] == ["a", "b"]


@respx.mock
async def test_the_async_client_sends_the_same_request(isolated_home):
    route = respx.delete(f"{BASE}/v1/projects/p1").respond(204)
    async with AsyncBeakr(base_url=BASE, api_key="bk_live_x") as client:
        assert await client.request("DELETE", "/v1/projects/p1", response="none") is None
    assert route.calls[0].request.headers["idempotency-key"]
