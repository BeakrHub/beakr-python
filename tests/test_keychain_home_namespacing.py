"""The keychain login is per ``BEAKR_HOME``; the default home keeps its old entry.

Root cause these guard (SPEC-162): the keychain entry was a fixed
``("beakr-cli", "device-tokens")`` while config, the fallback file and the
refresh lock live under ``BEAKR_HOME``, so two profiles shared -- and rotated --
one login. Only a fake in-memory ``keyring`` module is used, never the OS
keychain (see test_credentials_real_keyring.py for the opt-in real one).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from beakr_sdk import credentials
from beakr_sdk.credentials import StoredTokens, TokenStore, resolve_credential

ORIGIN = "https://api.test"


@pytest.fixture
def keychain(monkeypatch) -> dict:
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            set_password=lambda s, u, v: store.__setitem__((s, u), v),
            get_password=lambda s, u: store.get((s, u)),
            delete_password=lambda s, u: store.pop((s, u), None),
        ),
    )
    monkeypatch.setattr(credentials, "_keyring_usable", lambda: True)
    monkeypatch.delenv("BEAKR_NO_KEYRING", raising=False)
    return store


def _pair(tag: str) -> StoredTokens:
    return StoredTokens(
        access_token=f"access-{tag}", refresh_token=f"refresh-{tag}", base_url=ORIGIN
    )


def _drop_fallback_file() -> None:
    # Prove a read came from the keychain, not the 0600 fallback file.
    TokenStore().file_path.unlink(missing_ok=True)


def test_two_homes_keep_separate_keychain_logins(keychain, monkeypatch, tmp_path) -> None:
    home_a, home_b = tmp_path / "profile-a", tmp_path / "profile-b"
    monkeypatch.setenv("BEAKR_HOME", str(home_a))
    TokenStore().save(_pair("a"))
    _drop_fallback_file()

    monkeypatch.setenv("BEAKR_HOME", str(home_b))
    assert TokenStore().load(ORIGIN) is None
    TokenStore().save(_pair("b"))
    _drop_fallback_file()
    assert TokenStore().load(ORIGIN).access_token == "access-b"

    monkeypatch.setenv("BEAKR_HOME", str(home_a))
    assert TokenStore().load(ORIGIN).access_token == "access-a"
    assert resolve_credential(base_url=ORIGIN).tokens.access_token == "access-a"
    assert len(keychain) == 2


def test_clearing_one_home_leaves_the_other_signed_in(keychain, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "a"))
    TokenStore().save(_pair("a"))
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "b"))
    TokenStore().save(_pair("b"))
    TokenStore().clear()
    assert TokenStore().load(ORIGIN) is None
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "a"))
    assert TokenStore().load(ORIGIN).access_token == "access-a"


def test_the_keychain_account_is_stable_for_a_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "p"))
    first = credentials._keyring_username()
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "p") + "/")
    assert credentials._keyring_username() == first
    monkeypatch.setenv("BEAKR_HOME", str(tmp_path / "q"))
    assert credentials._keyring_username() != first
    assert first.startswith("device-tokens:")


@pytest.mark.parametrize("explicit_default", [False, True])
def test_the_default_home_keeps_the_original_keychain_entry(
    keychain, monkeypatch, explicit_default
) -> None:
    # isolated_home points HOME at a temp dir; ~/.beakr is the default home,
    # whether BEAKR_HOME is unset or names that same directory.
    if explicit_default:
        monkeypatch.setenv("BEAKR_HOME", str(credentials.Path.home() / ".beakr"))
    else:
        monkeypatch.delenv("BEAKR_HOME", raising=False)
    assert credentials._keyring_username() == "device-tokens"
    TokenStore().save(_pair("default"))
    assert list(keychain) == [("beakr-cli", "device-tokens")]


def test_a_login_saved_before_namespacing_still_loads_in_the_default_home(
    keychain, monkeypatch
) -> None:
    monkeypatch.delenv("BEAKR_HOME", raising=False)
    TokenStore().save(_pair("legacy"))
    legacy_payload = keychain[("beakr-cli", "device-tokens")]
    keychain.clear()
    _drop_fallback_file()
    # Exactly what an older CLI left behind: one fixed keychain entry.
    keychain[("beakr-cli", "device-tokens")] = legacy_payload
    assert TokenStore().load(ORIGIN).access_token == "access-legacy"
