"""A service-account credentials file closes the SDK's auth chain (developer platform W6.1).

Spec order: explicit key, ``BEAKR_API_KEY``, the CLI login, then the service
account file. The file names its key and, optionally, the API it belongs to; a
key bound to one API is never sent to another, exactly as a stored login is not.
"""

from __future__ import annotations

import json
import stat

import httpx
import pytest
import respx

from beakr_sdk import AuthenticationError, Beakr
from beakr_sdk.credentials import (
    beakr_home,
    read_service_account_file,
    resolve_credential,
    write_service_account_file,
)

from .conftest import BASE


def _write(path, **fields) -> None:
    data = {"type": "beakr_service_account", "api_key": "bk_live_sa", **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_the_named_file_supplies_the_key(tmp_path, monkeypatch) -> None:
    path = tmp_path / "ci" / "beakr.json"
    _write(path, base_url=BASE)
    monkeypatch.setenv("BEAKR_SERVICE_ACCOUNT_FILE", str(path))
    credential = resolve_credential(base_url=BASE)
    assert credential.source == "service_account"
    assert credential.auth_headers() == {"Authorization": "Bearer bk_live_sa"}


def test_the_default_file_is_used_when_it_exists() -> None:
    _write(beakr_home() / "service-account.json")
    assert resolve_credential(base_url=BASE).token == "bk_live_sa"


def test_it_comes_last_in_the_chain(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sa.json"
    _write(path)
    monkeypatch.setenv("BEAKR_SERVICE_ACCOUNT_FILE", str(path))
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_env")
    assert resolve_credential(base_url=BASE).token == "bk_live_env"
    assert resolve_credential("bk_live_explicit", base_url=BASE).token == "bk_live_explicit"


def test_a_key_bound_to_another_api_is_not_sent(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sa.json"
    _write(path, base_url="https://api.thebeakr.com")
    monkeypatch.setenv("BEAKR_SERVICE_ACCOUNT_FILE", str(path))
    with pytest.raises(AuthenticationError, match="api.thebeakr.com"):
        resolve_credential(base_url=BASE)


@pytest.mark.parametrize(
    "content, message",
    [
        ("{not json", "not valid JSON"),
        (json.dumps({"api_key": "bk_live_x"}), "not a Beakr service account file"),
        (json.dumps({"type": "beakr_service_account", "api_key": "sk-other"}), "no api_key"),
    ],
)
def test_a_named_file_that_is_unusable_fails_loudly(tmp_path, monkeypatch, content, message):
    path = tmp_path / "sa.json"
    path.write_text(content)
    monkeypatch.setenv("BEAKR_SERVICE_ACCOUNT_FILE", str(path))
    with pytest.raises(AuthenticationError, match=message):
        resolve_credential(base_url=BASE)


def test_a_named_file_that_is_missing_fails_loudly(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_SERVICE_ACCOUNT_FILE", str(tmp_path / "absent.json"))
    with pytest.raises(AuthenticationError, match="Cannot read the service account file"):
        resolve_credential(base_url=BASE)


def test_written_files_are_private_and_read_back(tmp_path) -> None:
    minted = {
        "id": "key-1",
        "key": "bk_test_minted",
        "name": "deploy",
        "user_id": "sa-1",
        "scopes": ["kb:read"],
        "expires_at": "2027-01-01T00:00:00+00:00",
    }
    path = write_service_account_file(tmp_path / "sa.json", minted, base_url=BASE + "/")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    account = read_service_account_file(path)
    assert (account.api_key, account.base_url, account.service_account_id, account.key_id) == (
        "bk_test_minted",
        BASE,
        "sa-1",
        "key-1",
    )
    with pytest.raises(ValueError, match="no key"):
        write_service_account_file(tmp_path / "x.json", {"id": "k"}, base_url=BASE)


@respx.mock
def test_a_client_authenticates_with_the_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sa.json"
    _write(path, base_url=BASE)
    monkeypatch.setenv("BEAKR_SERVICE_ACCOUNT_FILE", str(path))
    route = respx.get(f"{BASE}/v1/me").mock(return_value=httpx.Response(200, json={"user": {}}))
    with Beakr(base_url=BASE) as beakr:
        beakr.me.get()
    assert route.calls[0].request.headers["Authorization"] == "Bearer bk_live_sa"
