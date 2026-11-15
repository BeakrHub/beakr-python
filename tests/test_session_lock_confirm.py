"""Confirm before entering a locked project (engine ``max/lock-confirm``).

An automatic session that already worked in another project is held on its first
touch of a locked project: ``409 session_lock_confirm``, nothing read or written.
The SDK raises :class:`SessionLockConfirmError` naming the project, what entering
costs and the id to confirm, and ``client.confirming_lock(id)`` repeats the call
with ``X-Beakr-Confirm-Lock``.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from beakr_sdk import CONFIRM_LOCK_HEADER, ConflictError, SessionLockConfirmError

from .conftest import BASE

A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
MESSAGE = (
    "Heads-up: Partner A is siloed. Reading it limits this session's writes to Partner A, "
    "so it can no longer write to Chemistry (written earlier in this session). To go in, "
    f'repeat this call with confirm_lock="{A}"; or ask the user, or start a new session '
    "for Partner A."
)
FIX = f'Repeat this call with confirm_lock="{A}" (over HTTP, send X-Beakr-Confirm-Lock: {A}).'
COST = "can no longer write to Chemistry (written earlier in this session)"


def _held() -> httpx.Response:
    extras = {
        "session_id": "s1",
        "project": {"id": A, "name": "Partner A"},
        "cost": COST,
        "confirm_lock": A,
    }
    return httpx.Response(
        409,
        json={
            "detail": {"code": "session_lock_confirm", "message": MESSAGE, "fix": FIX, **extras},
            "error": {"code": "session_lock_confirm", "message": MESSAGE, "fix": FIX, **extras},
        },
    )


def test_a_held_call_raises_the_error_naming_the_project_cost_and_id(client):
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/me").mock(return_value=_held())
        with pytest.raises(SessionLockConfirmError) as caught:
            client.me.get()
    error = caught.value
    assert isinstance(error, ConflictError) and error.status == 409
    assert error.code == "session_lock_confirm"
    assert error.message == MESSAGE and error.fix == FIX
    assert error.project == {"id": A, "name": "Partner A"}
    assert (error.project_id, error.project_name) == (A, "Partner A")
    assert error.cost == COST
    assert error.confirm_lock == A and error.session_id == "s1"


def test_the_extras_are_read_from_detail_when_the_envelope_lacks_them(client):
    body = _held().json()
    body["error"] = {"code": "session_lock_confirm", "message": MESSAGE, "fix": FIX}
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/me").mock(return_value=httpx.Response(409, json=body))
        with pytest.raises(SessionLockConfirmError) as caught:
            client.me.get()
    assert caught.value.confirm_lock == A and caught.value.cost == COST


def test_confirming_lock_sends_the_header_inside_the_block_only(client):
    with respx.mock(base_url=BASE) as router:
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={}))
        client.me.get()
        with client.confirming_lock(A):
            client.me.get()
            with client.confirming_lock(B, A):
                client.me.get()
        client.me.get()
    sent = [call.request.headers.get(CONFIRM_LOCK_HEADER) for call in me.calls]
    assert sent == [None, A, f"{A},{B}", None]


def test_the_confirmed_retry_goes_in(client):
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/me").mock(
            side_effect=lambda request: (
                httpx.Response(200, json={"id": "u1"})
                if request.headers.get(CONFIRM_LOCK_HEADER) == A
                else _held()
            )
        )
        with pytest.raises(SessionLockConfirmError) as caught:
            client.me.get()
        with client.confirming_lock(caught.value.confirm_lock):
            assert client.me.get() == {"id": "u1"}


async def test_the_async_client_confirms_per_task(async_client):
    with respx.mock(base_url=BASE) as router:
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={}))

        async def confirmed() -> None:
            with async_client.confirming_lock(A):
                await asyncio.sleep(0)
                await async_client.me.get()

        async def plain() -> None:
            await asyncio.sleep(0)
            await async_client.me.get()

        await asyncio.gather(confirmed(), plain())
    sent = sorted(str(call.request.headers.get(CONFIRM_LOCK_HEADER)) for call in me.calls)
    assert sent == sorted([A, "None"])
