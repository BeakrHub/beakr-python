"""Opt-in: a device-flow login round-trips through the real OS keychain.

Every other test runs with ``BEAKR_NO_KEYRING=1`` or a fake keyring backend, so
the actual backend (macOS Keychain, Secret Service, Windows Credential Locker)
is otherwise never exercised. Run with ``BEAKR_TEST_REAL_KEYRING=1``. The test
writes under a unique throwaway service name, never the CLI's real ``beakr-cli``
entry, and deletes every account it may have touched afterwards. It also checks
that the entry is namespaced by ``BEAKR_HOME`` in the real backend.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import respx

from beakr_sdk import Beakr
from beakr_sdk import credentials as credentials_module
from beakr_sdk.credentials import StoredTokens, TokenStore, resolve_credential

from .conftest import BASE

#: The real HOME, read at import (before the autouse isolated_home fixture
#: swaps it). The macOS Security framework finds the login keychain through
#: HOME; under the temporary HOME it would try to create a new keychain and
#: block on a system dialog.
_REAL_HOME = os.environ.get("HOME")

pytestmark = pytest.mark.skipif(
    os.environ.get("BEAKR_TEST_REAL_KEYRING") != "1",
    reason="touches the real OS keychain; set BEAKR_TEST_REAL_KEYRING=1 to run",
)


@pytest.fixture
def real_keychain_service(monkeypatch):
    keyring = pytest.importorskip("keyring")
    if not credentials_module._keyring_usable():
        pytest.skip(f"no usable keyring backend ({keyring.get_keyring()!r})")
    if _REAL_HOME:
        # BEAKR_HOME stays the temporary directory, so config, the fallback file
        # and the keychain account (namespaced by BEAKR_HOME) are still isolated.
        monkeypatch.setenv("HOME", _REAL_HOME)
    service = f"beakr-cli-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(credentials_module, "_KEYRING_SERVICE", service)
    monkeypatch.delenv("BEAKR_NO_KEYRING", raising=False)
    # Every account the test may write: the default one and this home's,
    # recorded now because a test may switch BEAKR_HOME before teardown.
    accounts = {credentials_module._KEYRING_USERNAME, credentials_module._keyring_username()}
    yield service
    accounts.add(credentials_module._keyring_username())
    for account in accounts:
        try:
            keyring.delete_password(service, account)
        except Exception:
            pass
    for account in accounts:
        assert keyring.get_password(service, account) is None


@respx.mock
def test_login_saves_loads_refreshes_and_clears_through_the_real_keychain(
    real_keychain_service,
) -> None:
    import keyring

    store = TokenStore()
    assert store.backend == "keyring"
    store.save(
        StoredTokens(
            access_token="bk_at_old",
            refresh_token="bk_rt_old",
            expires_at=time.time() - 10,
            org_id="org-1",
            base_url=BASE,
        )
    )
    # The test runs under a temporary BEAKR_HOME, so the entry is that home's
    # namespaced account, not the default home's.
    account = credentials_module._keyring_username()
    assert account != credentials_module._KEYRING_USERNAME
    raw = keyring.get_password(real_keychain_service, account)
    assert raw is not None and "bk_rt_old" in raw
    assert keyring.get_password(real_keychain_service, credentials_module._KEYRING_USERNAME) is None
    # The fallback file is not what the reads below see.
    store.file_path.unlink(missing_ok=True)
    # A fresh store (a new process, in effect) reads the login back.
    loaded = TokenStore().load(BASE)
    assert loaded is not None and loaded.refresh_token == "bk_rt_old"
    assert resolve_credential(base_url=BASE).source == "cli_tokens"

    respx.post(f"{BASE}/v1/auth/token/refresh").respond(
        json={"access_token": "bk_at_new", "refresh_token": "bk_rt_new", "expires_in": 3600}
    )
    me = respx.get(f"{BASE}/v1/me").respond(json={})
    with Beakr(base_url=BASE) as beakr:
        beakr.me.get()
    assert me.calls.last.request.headers["authorization"] == "Bearer bk_at_new"
    rotated = TokenStore().load(BASE)
    assert (rotated.access_token, rotated.refresh_token) == ("bk_at_new", "bk_rt_new")

    TokenStore().clear(BASE)
    assert TokenStore().load(BASE) is None
    remaining = keyring.get_password(real_keychain_service, account)
    assert remaining is None or "bk_rt_new" not in remaining


def test_two_homes_keep_separate_logins_in_the_real_keychain(
    real_keychain_service, monkeypatch, tmp_path
) -> None:
    import keyring

    other_home = tmp_path / "second-profile"
    first_account = credentials_module._keyring_username()
    TokenStore().save(StoredTokens(access_token="bk_at_one", refresh_token="r1", base_url=BASE))
    monkeypatch.setenv("BEAKR_HOME", str(other_home))
    second_account = credentials_module._keyring_username()
    try:
        assert second_account != first_account
        assert TokenStore().load(BASE) is None
        TokenStore().save(StoredTokens(access_token="bk_at_two", refresh_token="r2", base_url=BASE))
        assert TokenStore().load(BASE).access_token == "bk_at_two"
        assert "bk_at_one" in (keyring.get_password(real_keychain_service, first_account) or "")
    finally:
        try:
            keyring.delete_password(real_keychain_service, second_account)
        except Exception:
            pass
    assert keyring.get_password(real_keychain_service, second_account) is None
