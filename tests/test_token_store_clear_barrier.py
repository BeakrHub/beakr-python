"""Logging out stays authoritative when the keychain cannot confirm the delete (audit C080).

Root cause: after a failed delete, ``clear`` re-read the keychain to check, and
treated a read *error* as "nothing left" -- so it removed the fallback file. A
temporarily locked keychain still held the previous login, and the next process
(once the keychain was readable again) got it back, spent refresh token and all.
A generation alone cannot fix this, because an unreadable keychain copy may carry
any generation; an unconfirmed clear now writes a time-ordered clear barrier that
every later write carries, and reads only consider copies under the newest one.
Only a fake in-memory ``keyring`` module is used, never the OS keychain.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from beakr_sdk import credentials
from beakr_sdk.credentials import StoredTokens, TokenStore

ORIGIN = "https://api.test"
OTHER = "https://sandbox.test"


def _slot() -> tuple[str, str]:
    return (credentials._KEYRING_SERVICE, credentials._keyring_username())


@pytest.fixture
def keychain(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "beakr"))
    state = SimpleNamespace(store={}, fail_set=False, fail_delete=False, fail_get=False)

    def set_password(service, username, value):
        if state.fail_set:
            raise RuntimeError("synthetic keychain write failure")
        state.store[service, username] = value

    def delete_password(service, username):
        if state.fail_delete:
            raise RuntimeError("synthetic keychain delete failure")
        state.store.pop((service, username), None)

    def get_password(service, username):
        if state.fail_get:
            raise RuntimeError("synthetic temporary keychain read failure")
        return state.store.get((service, username))

    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            set_password=set_password, get_password=get_password, delete_password=delete_password
        ),
    )
    monkeypatch.setattr(credentials, "_keyring_usable", lambda: True)
    return state


def _pair(tag: str, origin: str = ORIGIN) -> StoredTokens:
    return StoredTokens(
        access_token=f"access-{tag}", refresh_token=f"refresh-{tag}", base_url=origin
    )


def _recover(keychain) -> None:
    keychain.fail_set = keychain.fail_delete = keychain.fail_get = False


def test_a_clear_while_the_keychain_is_unreadable_survives_its_recovery(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_set = keychain.fail_delete = True
    store = TokenStore(use_keyring=True)
    store.save(_pair("new"))  # falls back to the file; the old pair stays in the keychain
    keychain.fail_get = True
    store.clear()
    _recover(keychain)
    assert keychain.store, "the stale keychain copy is still physically there"
    assert TokenStore(use_keyring=True).load(ORIGIN) is None


def test_a_hidden_higher_generation_keychain_login_cannot_outrank_the_clear(keychain):
    # Another process wrote a login with a high generation that this store
    # never saw because the keychain was unreadable during the clear.
    keychain.store[_slot()] = json.dumps(
        {"version": 2, "generation": 99, "logins": {ORIGIN: vars(_pair("hidden"))}}
    )
    keychain.fail_get = keychain.fail_delete = True
    TokenStore(use_keyring=True).clear()
    _recover(keychain)
    assert TokenStore(use_keyring=True).load(ORIGIN) is None


def test_an_older_post_clear_login_cannot_outrank_a_newer_clear(keychain):
    store = TokenStore(use_keyring=True)
    store.save(_pair("first"))
    keychain.fail_get = keychain.fail_delete = True
    store.clear()  # barrier A
    _recover(keychain)
    TokenStore(use_keyring=True).save(_pair("second"))  # carries barrier A, high generation
    keychain.fail_get = keychain.fail_delete = True
    TokenStore(use_keyring=True).clear()  # barrier B, generation not higher than the keychain's
    _recover(keychain)
    assert TokenStore(use_keyring=True).load(ORIGIN) is None


def test_delete_and_read_both_failing_leaves_a_logged_out_tombstone(keychain):
    TokenStore(use_keyring=True).save(_pair("only"))
    keychain.fail_delete = keychain.fail_get = True
    TokenStore(use_keyring=True).clear()
    tombstone = json.loads(TokenStore(use_keyring=False).file_path.read_text())
    assert tombstone["logins"] == {} and tombstone["cleared"]["nonce"]
    _recover(keychain)
    assert TokenStore(use_keyring=True).logins() == {}


def test_a_new_login_after_the_clear_wins_and_the_tombstone_goes(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_delete = keychain.fail_get = True
    TokenStore(use_keyring=True).clear()
    _recover(keychain)
    TokenStore(use_keyring=True).save(_pair("fresh"))
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-fresh"
    assert not TokenStore(use_keyring=False).file_path.exists()


def test_a_login_saved_while_the_keychain_cannot_be_read_back_is_kept_in_the_file(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_delete = keychain.fail_get = True
    TokenStore(use_keyring=True).clear()
    TokenStore(use_keyring=True).save(_pair("fresh"))  # written, but not confirmable
    assert json.loads(TokenStore(use_keyring=False).file_path.read_text())["after_clear"]
    _recover(keychain)
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-fresh"


def test_clearing_one_origin_keeps_the_others_under_the_barrier(keychain):
    store = TokenStore(use_keyring=True)
    store.save(_pair("a"))
    store.save(_pair("b", OTHER))
    keychain.fail_delete = keychain.fail_get = True
    TokenStore(use_keyring=True).clear()
    _recover(keychain)
    fresh = TokenStore(use_keyring=True)
    fresh.save(_pair("a2"))
    fresh.save(_pair("b2", OTHER))
    fresh.clear(OTHER)
    reread = TokenStore(use_keyring=True)
    assert reread.load(OTHER) is None
    assert reread.load(ORIGIN).refresh_token == "refresh-a2"


def test_a_confirmed_clear_leaves_no_file_and_no_barrier(keychain):
    TokenStore(use_keyring=True).save(_pair("x"))
    TokenStore(use_keyring=True).clear()
    assert not keychain.store
    assert not TokenStore(use_keyring=False).file_path.exists()
