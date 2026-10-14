"""Shared fixtures for SDK tests.

Every test is kept away from the developer's real credentials: the SDK reads
``~/.beakr``, the OS keychain and the ``BEAKR_*`` variables, and a test that
forgot to isolate one of them would read a real login (and could write over
it), so isolation is applied to every test rather than opted into. The
fixtures below give a client pointed at a fake host that never sleeps.
"""

from __future__ import annotations

import pytest

from beakr_sdk import AsyncBeakr, Beakr

BASE = "https://api.test"

_CREDENTIAL_ENV = (
    "BEAKR_API_KEY",
    "BEAKR_API_URL",
    "BEAKR_BASE_URL",
    "BEAKR_ORG_ID",
    "BEAKR_DEV_IDENTITY",
    "BEAKR_DEV_EMAIL",
    "BEAKR_DEV_IDENTITY_NAME",
    "BEAKR_DEV_DISPLAY_NAME",
    "BEAKR_SERVICE_ACCOUNT_FILE",
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BEAKR_HOME", str(home / ".beakr"))
    monkeypatch.setenv("BEAKR_NO_KEYRING", "1")
    for name in _CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps):
    beakr = Beakr(api_key="bk_live_test", base_url=BASE)
    beakr._sleep = sleeps.append
    yield beakr
    beakr.close()


@pytest.fixture
async def async_client(sleeps):
    beakr = AsyncBeakr(api_key="bk_live_test", base_url=BASE)

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    beakr._sleep = record
    yield beakr
    await beakr.close()
