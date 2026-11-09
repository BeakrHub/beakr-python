"""Sessions: a client confined to the projects a person chooses, with its token kept fresh."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from beakr_sdk import (
    AsyncBeakr,
    Beakr,
    SessionNeedsSelectionError,
    SessionWidenRefusedError,
    SessionWriteScopeError,
)

from .conftest import BASE


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _session(status: str = "active", **extra) -> dict:
    return {
        "id": "s1",
        "status": status,
        "client_name": "sdk-python",
        "picker_url": "https://app.test/sessions/s1",
        "read_projects": [{"id": "p1", "name": "Docs"}],
        "write_projects": [],
        "run_label": "session:s1",
        **extra,
    }


def _created(token: str = "bk_st_first", expires: timedelta = timedelta(hours=1)) -> dict:
    return _session(token={"token": token, "token_type": "bearer", "expires_at": _iso(expires)})


def _refusal(code: str, **details) -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "detail": {"code": code, "message": f"{code} message", **details},
            "error": {"code": code, "message": f"{code} message", "details": details},
        },
    )


def test_create_returns_a_client_that_sends_the_session_token(client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_created())
        )
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={"id": "u1"}))
        scoped = client.sessions.create(
            read=["p1", "p2"], write=["p2"], label="nightly", folder="/w"
        )
        scoped.client.me.get()
    body = json.loads(created.calls[0].request.content)
    assert body == {
        "client_name": "sdk-python",
        "label": "nightly",
        "folder": "/w",
        "read_project_ids": ["p1", "p2"],
        "write_project_ids": ["p2"],
        "issue_token": True,
    }
    # A retried create would start a second session.
    assert "idempotency-key" not in created.calls[0].request.headers
    assert scoped.id == "s1" and scoped.picker_url.endswith("/sessions/s1")
    assert "token" not in scoped.session
    assert me.calls[0].request.headers["authorization"] == "Bearer bk_st_first"
    # The person's own client is untouched.
    assert client.credential.token == "bk_live_test"


def test_write_alone_reads_the_same_projects(client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_created())
        )
        client.sessions.create(write=["p2"])
    body = json.loads(created.calls[0].request.content)
    assert body["read_project_ids"] == ["p2"] and body["write_project_ids"] == ["p2"]


def test_the_scoped_client_renews_its_token_before_it_expires(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_created(expires=timedelta(seconds=5)))
        )
        minted = router.post("/v1/sessions/s1/tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "bk_st_second", "expires_at": _iso(timedelta(hours=1))}
            )
        )
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={"id": "u1"}))
        scoped = client.sessions.create(read=["p1"], ttl_seconds=600)
        scoped.client.me.get()
        scoped.client.me.get()
    # Minted once, with the person's credential, before the first request went out.
    assert minted.call_count == 1
    assert minted.calls[0].request.headers["authorization"] == "Bearer bk_live_test"
    assert json.loads(minted.calls[0].request.content) == {"ttl_seconds": 600}
    assert [c.request.headers["authorization"] for c in me.calls] == ["Bearer bk_st_second"] * 2


def test_a_rejected_token_is_renewed_once_and_the_request_resent(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(return_value=httpx.Response(201, json=_created()))
        router.post("/v1/sessions/s1/tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "bk_st_second", "expires_at": _iso(timedelta(hours=1))}
            )
        )
        me = router.get("/v1/me").mock(
            side_effect=[
                httpx.Response(401, json={"detail": "expired"}),
                httpx.Response(200, json={}),
            ]
        )
        scoped = client.sessions.create(read=["p1"])
        scoped.client.me.get()
    assert [c.request.headers["authorization"] for c in me.calls] == [
        "Bearer bk_st_first",
        "Bearer bk_st_second",
    ]


def test_session_refusals_are_typed_with_the_picker(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(return_value=httpx.Response(201, json=_created()))
        router.get("/v1/me").mock(
            return_value=_refusal(
                "session_needs_selection", picker_url="https://app.test/sessions/s1"
            )
        )
        router.post("/v1/folders").mock(
            return_value=_refusal(
                "session_write_scope",
                picker_url="https://app.test/sessions/s1",
                write_project_ids=["p2"],
            )
        )
        router.patch("/v1/sessions/s1").mock(return_value=_refusal("session_widen_refused"))
        scoped = client.sessions.create()
        with pytest.raises(SessionNeedsSelectionError) as pending:
            scoped.client.me.get()
        assert pending.value.picker_url == "https://app.test/sessions/s1"
        with pytest.raises(SessionWriteScopeError) as write:
            scoped.client.request("POST", "/v1/folders", json={"name": "x", "project_id": "p1"})
        assert write.value.write_project_ids == ["p2"]
        with pytest.raises(SessionWidenRefusedError):
            scoped.client.sessions.update("s1", read=["p1", "p9"])


def test_scoped_wraps_an_existing_session(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions/s1/tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "bk_st_x", "expires_at": _iso(timedelta(hours=1))}
            )
        )
        router.get("/v1/sessions/s1").mock(return_value=httpx.Response(200, json=_session()))
        scoped = client.sessions.scoped("s1")
    assert scoped.client.credential.token == "bk_st_x"
    assert scoped.status == "active"


def test_update_end_list_and_current_send_their_operations(client):
    with respx.mock(base_url=BASE) as router:
        patched = router.patch("/v1/sessions/s1").mock(
            return_value=httpx.Response(200, json=_session())
        )
        router.post("/v1/sessions/s1/end").mock(
            return_value=httpx.Response(200, json=_session("ended"))
        )
        listed = router.get("/v1/sessions").mock(
            return_value=httpx.Response(200, json={"data": [_session()], "has_more": False})
        )
        router.get("/v1/sessions/current").mock(return_value=httpx.Response(200, json=_session()))
        client.sessions.update("s1", read=["p1"], write=[], remember_folder=True)
        assert client.sessions.end("s1")["status"] == "ended"
        assert [s["id"] for s in client.sessions.list(include_ended=True)] == ["s1"]
        assert client.sessions.current()["id"] == "s1"
    assert json.loads(patched.calls[0].request.content) == {
        "read_project_ids": ["p1"],
        "write_project_ids": [],
        "remember_folder": True,
    }
    assert listed.calls[0].request.url.params["include_ended"] == "true"


async def test_the_async_client_hands_out_an_async_scoped_client(async_client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_created(expires=timedelta(seconds=1)))
        )
        router.post("/v1/sessions/s1/tokens").mock(
            return_value=httpx.Response(
                201, json={"token": "bk_st_second", "expires_at": _iso(timedelta(hours=1))}
            )
        )
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={}))
        scoped = await async_client.sessions.create(read=["p1"])
        assert isinstance(scoped.client, AsyncBeakr)
        await scoped.client.me.get()
    assert me.calls[0].request.headers["authorization"] == "Bearer bk_st_second"


def test_the_scoped_client_shares_the_connection_pool(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(return_value=httpx.Response(201, json=_created()))
        scoped = client.sessions.create(read=["p1"])
    assert isinstance(scoped.client, Beakr)
    assert scoped.client._http is client._http
    scoped.client.close()
    assert not client._http.is_closed
