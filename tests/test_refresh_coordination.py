"""Stored logins are bound to their API origin and refreshed once across clients.

Root causes guarded (Codex review C014):

- Two clients sharing one stored login each refreshed with the same refresh
  token. Refresh rotates, and the engine revokes the whole login family when a
  spent refresh token is presented again, so the second refresh logged the
  person out everywhere.
- A login stored for one API was attached to requests for any other API the
  client was pointed at (a sandbox, a local server), and refreshed there.
"""

from __future__ import annotations

import asyncio
import json
import stat
import subprocess
import sys
import textwrap
import time
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, AuthenticationError, Beakr
from beakr_sdk.credentials import (
    StoredTokens,
    TokenStore,
    beakr_home,
    normalize_origin,
    refresh_lock_path,
    resolve_credential,
)
from beakr_sdk.errors import APIConnectionError

from .conftest import BASE

SANDBOX = "https://api.sandbox.test"
#: The OAuth issuer's token endpoint, stored with the login; refresh goes there.
REFRESH = "https://issuer.test/oauth/token"


def _login(*, base_url: str = BASE, access="bk_at_old", refresh="bk_rt_old", expired=True):
    TokenStore().save(
        StoredTokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=time.time() + (-10 if expired else 3600),
            base_url=base_url,
            org_id="org-1",
            issuer="https://issuer.test",
            token_endpoint=REFRESH,
            oauth_client_id="cli",
        )
    )


def _rotated(access="bk_at_new", refresh="bk_rt_new") -> dict:
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}


# -- origin binding ------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "requested", "same"),
    [
        ("https://api.test", "https://API.test/", True),
        ("https://api.test", "https://api.test:443", True),
        ("http://localhost:8000", "http://localhost:8000/", True),
        ("http://localhost:8000", "http://127.0.0.1:8000", False),
        ("http://localhost:8000", "http://localhost:8001", False),
        ("https://api.test", "https://api.sandbox.test", False),
        ("https://api.test", "http://api.test", False),
    ],
)
def test_origin_normalization(stored, requested, same) -> None:
    assert (normalize_origin(stored) == normalize_origin(requested)) is same


@respx.mock
def test_a_stored_login_is_not_sent_to_another_api() -> None:
    _login(expired=False)
    sandbox = respx.get(f"{SANDBOX}/v1/me").respond(json={})
    with Beakr(base_url=SANDBOX) as beakr, pytest.raises(AuthenticationError) as caught:
        beakr.me.get()
    assert "https://api.test" in str(caught.value) and not sandbox.called


@respx.mock
def test_each_origin_keeps_its_own_login() -> None:
    _login(base_url=BASE, access="bk_at_prod", expired=False)
    _login(base_url="http://localhost:8000", access="bk_at_local", expired=False)
    prod = respx.get(f"{BASE}/v1/me").respond(json={})
    local = respx.get("http://localhost:8000/v1/me").respond(json={})
    with Beakr(base_url=BASE) as beakr:
        beakr.me.get()
    with Beakr(base_url="http://localhost:8000") as beakr:
        beakr.me.get()
    assert prod.calls.last.request.headers["authorization"] == "Bearer bk_at_prod"
    assert local.calls.last.request.headers["authorization"] == "Bearer bk_at_local"


@respx.mock
def test_an_explicit_credential_is_the_callers_choice() -> None:
    _login(expired=False)
    sandbox = respx.get(f"{SANDBOX}/v1/me").respond(json={})
    with Beakr(api_key="bk_live_mine", base_url=SANDBOX) as beakr:
        beakr.me.get()
    assert sandbox.calls.last.request.headers["authorization"] == "Bearer bk_live_mine"


def test_the_saved_api_key_is_bound_to_its_api() -> None:
    home = beakr_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"api_key": "bk_live_k", "api_url": BASE}))
    assert resolve_credential(base_url=BASE).token == "bk_live_k"
    with pytest.raises(AuthenticationError):
        resolve_credential(base_url=SANDBOX)


def test_a_version_one_payload_is_read_as_its_origins_login() -> None:
    path = TokenStore().file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"access_token": "bk_at_v1", "refresh_token": "bk_rt_v1", "base_url": BASE})
    )
    assert TokenStore().load(BASE).access_token == "bk_at_v1"
    assert TokenStore().load(SANDBOX) is None


def test_store_writes_are_owner_only_and_atomic() -> None:
    _login()
    _login(base_url=SANDBOX)
    path = TokenStore().file_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not list(path.parent.glob(".credentials.*.tmp"))
    assert set(TokenStore().logins()) == {"https://api.test", "https://api.sandbox.test"}


# -- refresh coordination --------------------------------------------------------


@respx.mock
def test_two_clients_refresh_the_shared_login_once() -> None:
    _login()
    refresh = respx.post(REFRESH).respond(json=_rotated())
    me = respx.get(f"{BASE}/v1/me").respond(json={})
    first, second = Beakr(base_url=BASE), Beakr(base_url=BASE)
    # Both resolved the same stale pair before either refreshed.
    assert first.credential.tokens.refresh_token == second.credential.tokens.refresh_token
    first.me.get()
    second.me.get()
    first.close()
    second.close()
    assert [parse_qs(c.request.content.decode())["refresh_token"][0] for c in refresh.calls] == [
        "bk_rt_old"
    ]
    assert {c.request.headers["authorization"] for c in me.calls} == {"Bearer bk_at_new"}


@respx.mock
def test_two_async_clients_refresh_the_shared_login_once() -> None:
    _login()
    refresh = respx.post(REFRESH).respond(json=_rotated())
    me = respx.get(f"{BASE}/v1/me").respond(json={})

    async def run() -> None:
        first, second = AsyncBeakr(base_url=BASE), AsyncBeakr(base_url=BASE)
        assert first.credential.tokens.refresh_token == second.credential.tokens.refresh_token
        await asyncio.gather(first.me.get(), second.me.get())
        await first.close()
        await second.close()

    asyncio.run(run())
    assert len(refresh.calls) == 1
    assert {c.request.headers["authorization"] for c in me.calls} == {"Bearer bk_at_new"}


@respx.mock
def test_a_pair_rotated_by_another_process_is_adopted_not_respent(tmp_path) -> None:
    """A real second process holds the lock, rotates the stored pair, then releases."""
    _login()
    refresh = respx.post(REFRESH).respond(json=_rotated("bk_at_wrong", "bk_rt_wrong"))
    me = respx.get(f"{BASE}/v1/me").respond(json={})
    ready = tmp_path / "ready"
    script = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from beakr_sdk.credentials import RefreshLock, StoredTokens, TokenStore
        lock = RefreshLock()
        lock.acquire()
        Path({str(ready)!r}).write_text("locked")
        time.sleep(0.5)
        TokenStore().save(StoredTokens(
            access_token="bk_at_other", refresh_token="bk_rt_other",
            expires_at=time.time() + 3600, base_url={BASE!r}, org_id="org-1",
            issuer="https://issuer.test", token_endpoint={REFRESH!r}, oauth_client_id="cli"))
        lock.release()
        """
    )
    holder = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "lock holder did not start"
        with Beakr(base_url=BASE) as beakr:
            beakr.me.get()
    finally:
        holder.wait(timeout=10)
    assert not refresh.called
    assert me.calls.last.request.headers["authorization"] == "Bearer bk_at_other"
    assert refresh_lock_path().exists()


@respx.mock
def test_a_lost_refresh_answer_is_never_retried() -> None:
    _login()
    refresh = respx.post(REFRESH).mock(side_effect=httpx.ReadTimeout("no answer"))
    respx.get(f"{BASE}/v1/me").respond(json={})
    with Beakr(base_url=BASE) as beakr, pytest.raises(AuthenticationError) as caught:
        beakr.me.get()
    assert caught.value.code == "refresh_uncertain"
    assert TokenStore().load(BASE).refresh_state == "uncertain"
    # A later client, even in another process, does not present the token again.
    with Beakr(base_url=BASE) as beakr, pytest.raises(AuthenticationError) as again:
        beakr.me.get()
    assert again.value.code == "refresh_uncertain"
    assert len(refresh.calls) == 1


@respx.mock
def test_a_5xx_refresh_answer_is_treated_as_unknown() -> None:
    _login()
    refresh = respx.post(REFRESH).respond(502)
    with Beakr(base_url=BASE) as beakr, pytest.raises(AuthenticationError) as caught:
        beakr.me.get()
    assert caught.value.code == "refresh_uncertain" and len(refresh.calls) == 1


@respx.mock
def test_a_refresh_that_never_left_keeps_the_login_usable() -> None:
    _login()
    respx.post(REFRESH).mock(side_effect=httpx.ConnectError("offline"))
    with Beakr(base_url=BASE) as beakr, pytest.raises(APIConnectionError):
        beakr.me.get()
    assert TokenStore().load(BASE).refresh_state is None


@respx.mock
def test_a_refused_refresh_asks_for_a_new_sign_in() -> None:
    _login()
    respx.post(REFRESH).respond(400, json={"error": "invalid_grant"})
    with Beakr(base_url=BASE) as beakr, pytest.raises(AuthenticationError) as caught:
        beakr.me.get()
    assert caught.value.code == "invalid_grant"


@respx.mock
def test_refresh_goes_to_the_token_endpoint_stored_with_the_login() -> None:
    _login()
    refresh = respx.post(REFRESH).respond(json=_rotated())
    respx.get(f"{BASE}/v1/me").respond(json={})
    with Beakr(base_url="https://API.test/") as beakr:
        beakr.me.get()
    assert str(refresh.calls.last.request.url) == REFRESH
