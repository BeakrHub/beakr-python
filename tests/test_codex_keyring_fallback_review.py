"""A failed keychain update must not resurrect the previous refresh token."""

import sys
from types import SimpleNamespace

import pytest

from beakr_sdk import credentials


@pytest.mark.parametrize("fail_rotation_write", [False, True])
@pytest.mark.parametrize("clear_after_fallback", [False, True])
@pytest.mark.parametrize("unreadable_during_clear", [False, True])
@pytest.mark.parametrize("clear_specific_origin", [False, True])
def test_new_store_loads_rotated_tokens_after_keyring_write_fallback(
    monkeypatch,
    fail_rotation_write,
    clear_after_fallback,
    unreadable_during_clear,
    clear_specific_origin,
):
    keychain = {}
    failing = False
    unreadable = False

    def set_password(service, username, value):
        if failing:
            raise RuntimeError("Synthetic keychain write failure")
        keychain[service, username] = value

    def delete_password(service, username):
        if failing:
            raise RuntimeError("Synthetic keychain delete failure")
        keychain.pop((service, username), None)

    def get_password(service, username):
        if unreadable:
            raise RuntimeError("Synthetic temporary keychain read failure")
        return keychain.get((service, username))

    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            set_password=set_password,
            get_password=get_password,
            delete_password=delete_password,
        ),
    )
    monkeypatch.setattr(credentials, "_keyring_usable", lambda: True)
    store = credentials.TokenStore(use_keyring=True)
    origin = "https://api.test"
    store.save(
        credentials.StoredTokens(
            access_token="synthetic-old-access",
            refresh_token="synthetic-old-refresh",
            base_url=origin,
        )
    )
    failing = fail_rotation_write
    store.save(
        credentials.StoredTokens(
            access_token="synthetic-new-access",
            refresh_token="synthetic-new-refresh",
            base_url=origin,
        )
    )
    if fail_rotation_write:
        assert store.backend == "file"
    if clear_after_fallback:
        unreadable = unreadable_during_clear
        # CLI auth logout clears a specific origin, not the whole store.
        store.clear(origin if clear_specific_origin else None)
    # A different command constructs its own store after the backend is usable again.
    failing = False
    unreadable = False
    loaded = credentials.TokenStore(use_keyring=True).load(origin)
    if clear_after_fallback:
        assert loaded is None, "Clearing fallback credentials resurrected a stale keychain login"
        return
    assert loaded is not None
    assert loaded.refresh_token == "synthetic-new-refresh", (
        "Fresh store resurrected the stale keychain pair over the rotated fallback file"
    )
