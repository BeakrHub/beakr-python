"""Which stored copy of a login is authoritative after a keychain fallback (audit C079).

Root cause: a failed keychain write put the rotated pair in the 0600 file, but a
fresh store read the keychain first and got the previous pair back -- whose
refresh token the server had already rotated away, so presenting it revokes
the login. Each write now carries a generation and reads take the newest copy.
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


@pytest.fixture
def keychain(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "beakr"))
    store: dict = {}
    state = SimpleNamespace(store=store, fail_set=False, fail_delete=False)

    def set_password(service, username, value):
        if state.fail_set:
            raise RuntimeError("synthetic keychain write failure")
        store[service, username] = value

    def delete_password(service, username):
        if state.fail_delete:
            raise RuntimeError("synthetic keychain delete failure")
        store.pop((service, username), None)

    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            set_password=set_password,
            get_password=lambda service, username: store.get((service, username)),
            delete_password=delete_password,
        ),
    )
    monkeypatch.setattr(credentials, "_keyring_usable", lambda: True)
    return state


def _pair(tag: str, origin: str = ORIGIN, **extra) -> StoredTokens:
    return StoredTokens(
        access_token=f"access-{tag}", refresh_token=f"refresh-{tag}", base_url=origin, **extra
    )


def test_a_failed_keychain_write_and_delete_still_leaves_the_rotated_pair_authoritative(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_set = keychain.fail_delete = True
    store = TokenStore(use_keyring=True)
    store.save(_pair("new"))
    assert store.backend == "file"
    keychain.fail_set = keychain.fail_delete = False
    # The keychain still holds the old pair; the file's higher generation wins.
    assert keychain.store, "the stale keychain copy is still there"
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-new"


def test_the_fallback_removes_the_stale_keychain_copy_when_it_can(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_set = True
    TokenStore(use_keyring=True).save(_pair("new"))
    keychain.fail_set = False
    assert not keychain.store
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-new"


def test_a_recovered_keychain_takes_the_newest_copy_back_and_the_file_goes(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_set = keychain.fail_delete = True
    TokenStore(use_keyring=True).save(_pair("new"))
    keychain.fail_set = keychain.fail_delete = False
    recovered = TokenStore(use_keyring=True)
    recovered.save(_pair("newer"))
    assert recovered.backend == "keyring"
    assert not recovered.file_path.exists()
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-newer"


def test_other_origins_logins_survive_the_fallback(keychain):
    TokenStore(use_keyring=True).save(_pair("prod"))
    TokenStore(use_keyring=True).save(_pair("sandbox", OTHER))
    keychain.fail_set = True
    TokenStore(use_keyring=True).save(_pair("prod-rotated"))
    keychain.fail_set = False
    fresh = TokenStore(use_keyring=True)
    assert fresh.load(ORIGIN).refresh_token == "refresh-prod-rotated"
    assert fresh.load(OTHER).refresh_token == "refresh-sandbox"


def test_an_uncertain_refresh_marker_saved_during_fallback_is_what_the_next_process_sees(keychain):
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_set = True
    TokenStore(use_keyring=True).save(_pair("old", refresh_state="uncertain"))
    keychain.fail_set = False
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_state == "uncertain"


def test_an_older_file_only_login_still_loads_and_migrates_into_the_keychain(keychain):
    legacy = TokenStore(use_keyring=True)
    legacy.file_path.parent.mkdir(parents=True, exist_ok=True)
    legacy.file_path.write_text(
        json.dumps({"access_token": "access-v1", "refresh_token": "refresh-v1", "base_url": ORIGIN})
    )
    assert legacy.load(ORIGIN).refresh_token == "refresh-v1"
    legacy.save(_pair("v2"))
    assert not legacy.file_path.exists()
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-v2"


def test_the_refresh_lock_is_independent_of_the_token_copies(keychain, tmp_path):
    keychain.fail_set = True
    TokenStore(use_keyring=True).save(_pair("only"))
    lock = credentials.RefreshLock(tmp_path / "refresh.lock")
    lock.acquire(timeout=1)
    assert lock.held
    lock.release()
    assert not lock.held


# -- clearing is authoritative across both backends (audit C080) ---------------
#
# Root cause: clear() on a store that had fallen back to the file deleted only
# the file, so a fresh store found the older keychain login again, with the
# refresh token the server had already rotated away.


def _fallback_with_stale_keychain(keychain) -> TokenStore:
    TokenStore(use_keyring=True).save(_pair("old"))
    keychain.fail_set = keychain.fail_delete = True
    store = TokenStore(use_keyring=True)
    store.save(_pair("new"))
    return store


def test_a_fallback_rotation_then_logout_leaves_no_login(keychain):
    store = _fallback_with_stale_keychain(keychain)
    keychain.fail_set = keychain.fail_delete = False
    store.clear()
    assert TokenStore(use_keyring=True).load(ORIGIN) is None


def test_logout_when_the_keychain_refuses_to_delete_leaves_a_tombstone(keychain):
    store = _fallback_with_stale_keychain(keychain)
    store.clear()  # keychain still refuses both delete and write
    assert keychain.store, "the stale keychain copy could not be removed"
    fresh = TokenStore(use_keyring=True)
    assert fresh.load(ORIGIN) is None
    assert fresh.logins() == {}


def test_a_new_login_after_a_tombstone_works_and_clears_it(keychain):
    store = _fallback_with_stale_keychain(keychain)
    store.clear()
    keychain.fail_set = keychain.fail_delete = False
    TokenStore(use_keyring=True).save(_pair("fresh"))
    assert TokenStore(use_keyring=True).load(ORIGIN).refresh_token == "refresh-fresh"
    assert not credentials.TokenStore(use_keyring=True).file_path.exists()


def test_clearing_one_origin_in_fallback_keeps_the_others(keychain):
    TokenStore(use_keyring=True).save(_pair("other", OTHER))
    store = _fallback_with_stale_keychain(keychain)
    store.clear(ORIGIN)
    fresh = TokenStore(use_keyring=True)
    assert fresh.load(ORIGIN) is None
    assert fresh.load(OTHER).refresh_token == "refresh-other"


def test_the_tombstone_is_an_empty_newer_generation(keychain):
    store = _fallback_with_stale_keychain(keychain)
    store.clear()
    payload = json.loads(store.file_path.read_text())
    stale = json.loads(next(iter(keychain.store.values())))
    assert payload["logins"] == {}
    assert payload["generation"] > stale["generation"]
