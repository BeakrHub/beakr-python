"""The credential chain, token storage, and refresh with rotation."""

from __future__ import annotations

import json
import stat
import time
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from beakr_sdk import AuthenticationError, Beakr
from beakr_sdk.credentials import (
    StoredTokens,
    TokenStore,
    beakr_home,
    resolve_base_url,
    resolve_credential,
)

from .conftest import BASE


def _write_config(data: dict) -> None:
    home = beakr_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps(data))


#: The OAuth issuer's token endpoint stored with the login.
ISSUER_TOKEN = "https://issuer.test/oauth/token"


def _store_tokens(*, access="bk_at_old", refresh="bk_rt_old", expires_in=3600) -> None:
    TokenStore().save(
        StoredTokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=time.time() + expires_in,
            org_id="org-1",
            user={"display_name": "Ada"},
            base_url=BASE,
            issuer="https://issuer.test",
            token_endpoint=ISSUER_TOKEN,
            oauth_client_id="cli",
        )
    )


def test_no_credentials_is_a_clear_authentication_error() -> None:
    with pytest.raises(AuthenticationError, match="beakr auth login"):
        resolve_credential()


def test_chain_order_explicit_env_dev_tokens_config(monkeypatch) -> None:
    # The spec's order (W6.1.4): explicit key, BEAKR_API_KEY, the CLI login,
    # the service account file. A dev identity sits after BEAKR_API_KEY.
    _write_config({"api_key": "bk_live_config", "api_url": BASE})
    assert resolve_credential().source == "cli_config"
    _store_tokens()
    assert resolve_credential().source == "cli_tokens"
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "id-1")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "dev@example.test")
    assert resolve_credential().auth_headers()["X-Identity-Id"] == "id-1"
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_env")
    assert resolve_credential().source == "env"
    assert resolve_credential().token == "bk_live_env"
    assert resolve_credential("bk_live_explicit").token == "bk_live_explicit"


def test_a_saved_api_key_lives_in_the_store_and_is_sent_as_a_plain_key() -> None:
    # Security (B4): the CLI kept a saved key in plain text in config.json
    # (0644). It now saves it in the token store (keychain, else a 0600 file);
    # the SDK reads it there as the saved key: a bearer that is never refreshed.
    TokenStore().save(StoredTokens.for_api_key("bk_live_saved", base_url=BASE))
    credential = resolve_credential(base_url=BASE)
    assert credential.source == "cli_config" and credential.token == "bk_live_saved"
    assert credential.kind == "api_key" and not credential.can_refresh
    assert credential.auth_headers() == {"Authorization": "Bearer bk_live_saved"}
    stored = (beakr_home() / "credentials").stat()
    assert stat.S_IMODE(stored.st_mode) == 0o600
    # Bound to its API like a sign-in: not offered to another origin.
    with pytest.raises(AuthenticationError, match="only sent to the API that issued it"):
        resolve_credential(base_url="https://other.test")


def test_base_url_order(monkeypatch) -> None:
    assert resolve_base_url() == "https://api.thebeakr.com"
    _write_config({"api_url": "https://config.test/"})
    assert resolve_base_url() == "https://config.test"
    monkeypatch.setenv("BEAKR_API_URL", "https://api-url.test")
    assert resolve_base_url() == "https://api-url.test"
    monkeypatch.setenv("BEAKR_BASE_URL", "https://base-url.test")
    assert resolve_base_url() == "https://base-url.test"
    assert resolve_base_url("https://explicit.test") == "https://explicit.test"


def test_token_file_is_private() -> None:
    _store_tokens()
    path = TokenStore().file_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert TokenStore().load(BASE).access_token == "bk_at_old"
    TokenStore().clear()
    assert TokenStore().load(BASE) is None


@respx.mock
def test_401_refreshes_once_and_persists_the_rotated_pair() -> None:
    _store_tokens()
    me = respx.get(f"{BASE}/v1/me").mock(
        side_effect=[httpx.Response(401, json={"detail": "expired"}), httpx.Response(200, json={})]
    )
    refresh = respx.post(ISSUER_TOKEN).respond(
        json={
            "access_token": "bk_at_new",
            "refresh_token": "bk_rt_new",
            "expires_in": 3600,
            "refresh_expires_in": 2592000,
        }
    )
    with Beakr(base_url=BASE) as beakr:
        beakr.me.get()
    assert parse_qs(refresh.calls.last.request.content.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["bk_rt_old"],
        "client_id": ["cli"],
    }
    assert "authorization" not in refresh.calls.last.request.headers
    assert me.calls[0].request.headers["authorization"] == "Bearer bk_at_old"
    assert me.calls[1].request.headers["authorization"] == "Bearer bk_at_new"
    stored = TokenStore().load(BASE)
    # Refresh rotates: the old refresh token is dead, so losing the new one logs out.
    assert (stored.access_token, stored.refresh_token) == ("bk_at_new", "bk_rt_new")
    assert stored.org_id == "org-1" and stored.user == {"display_name": "Ada"}


@respx.mock
def test_expired_access_token_is_refreshed_before_the_request() -> None:
    _store_tokens(expires_in=-10)
    respx.post(ISSUER_TOKEN).respond(
        json={"access_token": "bk_at_new", "refresh_token": "bk_rt_new", "expires_in": 3600}
    )
    me = respx.get(f"{BASE}/v1/me").respond(json={})
    with Beakr(base_url=BASE) as beakr:
        beakr.me.get()
    assert len(me.calls) == 1
    assert me.calls.last.request.headers["authorization"] == "Bearer bk_at_new"


@respx.mock
def test_a_second_401_after_refresh_is_raised_not_looped() -> None:
    _store_tokens()
    respx.get(f"{BASE}/v1/me").respond(401, json={"detail": "revoked"})
    refresh = respx.post(ISSUER_TOKEN).respond(
        json={"access_token": "a2", "refresh_token": "r2", "expires_in": 3600}
    )
    with Beakr(base_url=BASE) as beakr, pytest.raises(AuthenticationError):
        beakr.me.get()
    assert len(refresh.calls) == 1


@respx.mock
def test_api_keys_are_not_refreshed() -> None:
    refresh = respx.post(ISSUER_TOKEN)
    respx.get(f"{BASE}/v1/me").respond(401, json={"detail": "bad key"})
    with Beakr(api_key="bk_live_x", base_url=BASE) as beakr, pytest.raises(AuthenticationError):
        beakr.me.get()
    assert not refresh.called


@respx.mock
def test_explicit_tokens_refresh_without_touching_the_cli_store() -> None:
    _store_tokens(access="bk_at_cli", refresh="bk_rt_cli")
    with Beakr(api_key="bk_live_explicit", base_url=BASE) as beakr:
        respx.get(f"{BASE}/v1/me").respond(json={})
        beakr.me.get()
    assert TokenStore().load(BASE).access_token == "bk_at_cli"
