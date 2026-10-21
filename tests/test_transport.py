"""Headers, retries, error mapping and pagination of the shared transport."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_sdk import (
    APIConnectionError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ValidationError,
    __version__,
)
from beakr_sdk._calls import Call

from .conftest import BASE


@respx.mock
def test_requests_identify_client_agent_and_credential() -> None:
    from beakr_sdk import Beakr

    route = respx.get(f"{BASE}/v1/me").respond(json={"user": {}})
    with Beakr(api_key="bk_live_k", base_url=BASE, agent="claude-code/s1", org_id="org-1") as b:
        b.me.get()
    headers = route.calls.last.request.headers
    assert headers["authorization"] == "Bearer bk_live_k"
    assert headers["x-beakr-client"] == f"sdk-python/{__version__}"
    assert headers["x-beakr-agent"] == "claude-code/s1"
    assert headers["x-org-id"] == "org-1"
    assert "idempotency-key" not in headers


# X-Beakr-Session names the agent session behind a write (a Claude Code session,
# a Codex thread), so its provenance can say "session <id>" beside agent and run.
@respx.mock
def test_the_session_is_sent_from_the_option_or_beakr_session(monkeypatch) -> None:
    from beakr_sdk import Beakr

    route = respx.get(f"{BASE}/v1/me").respond(json={"user": {}})
    monkeypatch.setenv("BEAKR_SESSION", "env-session")
    with Beakr(api_key="bk_live_k", base_url=BASE) as b:
        b.me.get()
    assert route.calls.last.request.headers["x-beakr-session"] == "env-session"
    with Beakr(api_key="bk_live_k", base_url=BASE, session="cc-123") as b:
        b.me.get()
    assert route.calls.last.request.headers["x-beakr-session"] == "cc-123"
    with Beakr(api_key="bk_live_k", base_url=BASE, session="") as b:
        b.me.get()
    assert "x-beakr-session" not in route.calls.last.request.headers


@respx.mock
def test_no_session_header_without_a_session(monkeypatch) -> None:
    from beakr_sdk import AsyncBeakr, Beakr

    monkeypatch.delenv("BEAKR_SESSION", raising=False)
    route = respx.get(f"{BASE}/v1/me").respond(json={"user": {}})
    with Beakr(api_key="bk_live_k", base_url=BASE) as b:
        b.me.get()
    assert "x-beakr-session" not in route.calls.last.request.headers
    assert AsyncBeakr(api_key="bk_live_k", base_url=BASE).session is None


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@respx.mock
def test_keyed_creates_are_retried_on_5xx_with_the_same_key(client, sleeps, status) -> None:
    """The engine replays every public create on its Idempotency-Key (W1.2), so a
    create whose first attempt failed ambiguously is resent with the same key:
    the server returns the first result instead of creating a second project.
    Root cause guarded: the SDK refused to resend any create after a 5xx, citing a
    replay guarantee the engine does give (SPEC-440)."""
    route = respx.post(f"{BASE}/v1/projects").mock(
        side_effect=[httpx.Response(status), httpx.Response(201, json={"id": "p1"})]
    )
    assert client.projects.create("Research") == {"id": "p1"}
    keys = {call.request.headers["idempotency-key"] for call in route.calls}
    assert len(route.calls) == 2 and len(keys) == 1 and all(keys)
    bodies = {call.request.content for call in route.calls}
    assert len(bodies) == 1
    assert len(sleeps) == 1


@respx.mock
def test_keyed_creates_are_retried_after_a_lost_connection(client, sleeps) -> None:
    route = respx.post(f"{BASE}/v1/projects/p1/members").mock(
        side_effect=[httpx.ReadTimeout("timed out"), httpx.Response(201, json={"ok": True})]
    )
    assert client.request("POST", "/v1/projects/p1/members", json={"user_id": "u"}) == {"ok": True}
    keys = {call.request.headers["idempotency-key"] for call in route.calls}
    assert len(route.calls) == 2 and len(keys) == 1


@respx.mock
def test_keyed_create_retry_honors_retry_after(client, sleeps) -> None:
    respx.post(f"{BASE}/v1/projects").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "4"}),
            httpx.Response(201, json={"id": "p1"}),
        ]
    )
    client.projects.create("Research")
    assert sleeps == [4.0]


@respx.mock
def test_keyed_create_waits_out_an_attempt_that_still_holds_the_key(client, sleeps) -> None:
    """A resend can reach the engine while the first attempt still holds the key;
    it answers 409 idempotency_in_progress with Retry-After, meaning retry."""
    in_progress = {"detail": {"code": "idempotency_in_progress", "message": "retry"}}
    route = respx.post(f"{BASE}/v1/projects").mock(
        side_effect=[
            httpx.Response(504),
            httpx.Response(409, json=in_progress, headers={"Retry-After": "1"}),
            httpx.Response(201, json={"id": "p1"}),
        ]
    )
    assert client.projects.create("Research") == {"id": "p1"}
    assert len(route.calls) == 3
    assert sleeps[1] == 1.0


@respx.mock
def test_other_conflicts_on_a_keyed_create_are_not_retried(client) -> None:
    reused = {"error": {"code": "idempotency_key_reused", "message": "different body"}}
    route = respx.post(f"{BASE}/v1/projects").respond(409, json=reused)
    with pytest.raises(ConflictError):
        client.projects.create("Research")
    assert len(route.calls) == 1


@pytest.mark.parametrize(
    "path",
    [
        # A decision: a repeat is refused, so a resend could turn success into an error.
        "/v1/skills/s1/proposals/p1/accept",
        # Each call is a new rotation by design.
        "/v1/webhook-endpoints/w1/rotate-secret",
        # A read with a body; not a create and not keyed by the engine.
        "/v1/knowledge/wiki/command",
    ],
)
@respx.mock
def test_posts_the_engine_does_not_replay_are_not_retried_on_5xx(client, path) -> None:
    route = respx.post(f"{BASE}{path}").respond(503)
    with pytest.raises(ServerError):
        client.request("POST", path, json={})
    assert len(route.calls) == 1
    assert route.calls[0].request.headers["idempotency-key"]


@respx.mock
def test_reads_are_retried_on_500(client, sleeps) -> None:
    route = respx.get(f"{BASE}/v1/me").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"user": {}})]
    )
    client.me.get()
    assert len(route.calls) == 2


@pytest.mark.parametrize("status", [501, 505])
@respx.mock
def test_5xx_that_will_never_succeed_is_not_retried(client, status) -> None:
    route = respx.post(f"{BASE}/v1/projects").respond(status)
    with pytest.raises(ServerError):
        client.projects.create("Research")
    assert len(route.calls) == 1


@respx.mock
def test_creates_are_retried_on_429_with_the_same_key(client, sleeps) -> None:
    route = respx.post(f"{BASE}/v1/projects").mock(
        side_effect=[httpx.Response(429), httpx.Response(201, json={"id": "p1"})]
    )
    assert client.projects.create("Research") == {"id": "p1"}
    keys = {call.request.headers["idempotency-key"] for call in route.calls}
    assert len(route.calls) == 2 and len(keys) == 1
    assert len(sleeps) == 1


@respx.mock
def test_run_start_is_retried_because_the_route_deduplicates(client) -> None:
    route = respx.post(f"{BASE}/v1/runs").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"run_id": "r"})]
    )
    assert client.runs.start("hi") == {"run_id": "r"}
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert bodies[0]["idempotency_key"] == bodies[1]["idempotency_key"]


@respx.mock
def test_post_without_idempotency_is_not_retried_on_5xx(client) -> None:
    route = respx.post(f"{BASE}/v1/knowledge/wiki/command").respond(503)
    with pytest.raises(ServerError):
        client.knowledge.command("cat", page="X")
    assert len(route.calls) == 1


@respx.mock
def test_429_is_retried_honoring_retry_after(client, sleeps) -> None:
    respx.post(f"{BASE}/v1/knowledge/wiki/command").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"content": [], "outcome": {"status": "success"}}),
        ]
    )
    client.knowledge.command("ls")
    assert sleeps == [7.0]


@respx.mock
def test_retries_stop_at_max_retries(client, sleeps) -> None:
    route = respx.get(f"{BASE}/v1/me").respond(502)
    with pytest.raises(ServerError):
        client.me.get()
    assert len(route.calls) == client.max_retries + 1
    assert all(0 < delay <= 8 for delay in sleeps)


@respx.mock
def test_connection_errors_are_retried_then_raised(client) -> None:
    route = respx.get(f"{BASE}/v1/me").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError):
        client.me.get()
    assert len(route.calls) == client.max_retries + 1


@respx.mock
def test_other_4xx_is_never_retried(client) -> None:
    route = respx.get(f"{BASE}/v1/me").respond(400, json={"detail": "bad"})
    with pytest.raises(ValidationError):
        client.me.get()
    assert len(route.calls) == 1


@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        (401, AuthenticationError, "unauthorized"),
        (403, PermissionDeniedError, "forbidden"),
        (404, NotFoundError, "not_found"),
        (409, ConflictError, "conflict"),
        (422, ValidationError, "validation_error"),
        (500, ServerError, "internal_error"),
    ],
)
@respx.mock
def test_legacy_detail_errors_map_to_types(client, status, error_type, code) -> None:
    respx.get(f"{BASE}/v1/projects/p1").respond(
        status, json={"detail": "nope"}, headers={"X-Request-Id": "req-9"}
    )
    with pytest.raises(error_type) as caught:
        client.projects.get("p1")
    assert caught.value.status == status
    assert caught.value.code == code
    assert caught.value.message == "nope"
    assert caught.value.request_id == "req-9"


@respx.mock
def test_error_envelope_wins_and_insufficient_scope_names_the_scope(client) -> None:
    respx.post(f"{BASE}/v1/knowledge/wiki/write").respond(
        403,
        json={
            "detail": "legacy",
            "error": {
                "code": "insufficient_scope",
                "message": "Requires kb:write",
                "request_id": "req-1",
                "required_scope": "kb:write",
            },
        },
    )
    with pytest.raises(PermissionDeniedError) as caught:
        client.knowledge.write("add_fact", page="X")
    error = caught.value
    assert (error.code, error.message, error.request_id) == (
        "insufficient_scope",
        "Requires kb:write",
        "req-1",
    )
    assert error.required_scope == "kb:write"
    assert "req-1" in str(error)


@respx.mock
def test_validation_detail_list_is_readable(client) -> None:
    respx.post(f"{BASE}/v1/projects").respond(
        422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]}
    )
    with pytest.raises(ValidationError, match="name: field required"):
        client.projects.create("")


@respx.mock
def test_rate_limit_error_carries_retry_after_when_not_waited(client) -> None:
    from beakr_sdk import Beakr

    respx.get(f"{BASE}/v1/me").respond(429, headers={"Retry-After": "3600"})
    with Beakr(api_key="k", base_url=BASE) as b:
        with pytest.raises(RateLimitError) as caught:
            b.me.get()
    assert caught.value.retry_after == 3600


@respx.mock
def test_pagination_follows_next_cursor(client) -> None:
    route = respx.get(f"{BASE}/v1/conversations").mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [{"id": "a"}], "next_cursor": "c1", "has_more": True}
            ),
            httpx.Response(
                200, json={"data": [{"id": "b"}], "next_cursor": None, "has_more": False}
            ),
        ]
    )
    items = list(client.conversations.iter(client="codex", page_size=1))
    assert [item["id"] for item in items] == ["a", "b"]
    assert "cursor" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["cursor"] == "c1"
    assert route.calls[1].request.url.params["client"] == "codex"


@respx.mock
def test_external_calls_never_leak_beakr_headers(client) -> None:
    route = respx.put("https://bucket.s3.test/key").respond(200)
    client._execute(
        Call("PUT", "https://bucket.s3.test/key", content=b"x", external=True, response="none")
    )
    headers = route.calls.last.request.headers
    assert "authorization" not in headers
    assert "x-beakr-client" not in headers


@respx.mock
def test_sse_stream_yields_events_until_complete(client) -> None:
    body = (
        ": heartbeat\n\n"
        'id: 1-0\ndata: {"type": "text_delta", "data": {"text": "Hi"}}\n\n'
        'id: 2-0\ndata: {"type": "stream_complete", "data": {}}\n\n'
        'id: 3-0\ndata: {"type": "never"}\n\n'
    )
    respx.get(f"{BASE}/v1/runs/r1/events").respond(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )
    events = list(client.runs.stream("r1"))
    assert [e["type"] for e in events] == ["text_delta", "stream_complete"]
    assert events[0]["event_id"] == "1-0"


@respx.mock
def test_204_returns_none(client) -> None:
    respx.delete(f"{BASE}/v1/projects/p1").respond(204)
    assert client.projects.delete("p1") is None


@respx.mock
async def test_async_client_shares_behavior(async_client, sleeps) -> None:
    respx.get(f"{BASE}/v1/projects").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=[{"id": "p1"}])]
    )
    assert await async_client.projects.list() == [{"id": "p1"}]
    assert len(sleeps) == 1
    respx.get(f"{BASE}/v1/projects/zz").respond(404, json={"detail": "gone"})
    with pytest.raises(NotFoundError):
        await async_client.projects.get("zz")


@respx.mock
async def test_async_pagination_and_stream(async_client) -> None:
    async_client = async_client
    respx.get(f"{BASE}/v1/projects").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": "1"}], "next_cursor": "c", "has_more": True}),
            httpx.Response(200, json={"data": [{"id": "2"}], "has_more": False}),
        ]
    )
    seen = [project["id"] async for project in async_client.paginate("/v1/projects")]
    assert seen == ["1", "2"]
    respx.get(f"{BASE}/v1/runs/r/events").respond(
        200, content=b'data: {"type": "stream_complete"}\n\n'
    )
    assert [e["type"] async for e in async_client.runs.stream("r")] == ["stream_complete"]


def test_json_body_is_sent_as_json(client) -> None:
    request = client._build_request(Call("POST", "/v1/x", json={"a": 1}), None)
    assert json.loads(request.content) == {"a": 1}
    assert request.headers["content-type"] == "application/json"
