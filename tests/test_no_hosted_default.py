"""The self-hosted line has no hosted default: no URL configured, nothing is sent.

``Beakr(api_key=k)`` with no ``base_url``, no ``BEAKR_BASE_URL`` and no CLI config
once sent the key to ``https://api.thebeakr.com``. It now refuses before any
request, naming how to set the deployment's URL; a 401 names the API that
refused the key, and an HTML answer no longer points at the hosted URL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

import beakr_sdk
from beakr_sdk import AsyncBeakr, AuthenticationError, Beakr, UnexpectedResponseError, credentials
from beakr_sdk.credentials import beakr_home

MESSAGE = (
    "Set your deployment's URL: pass base_url=... or set BEAKR_BASE_URL "
    "(for example https://beakr.example.com)."
)


def test_message_is_the_one_people_read() -> None:
    assert credentials.BASE_URL_REQUIRED_MESSAGE == MESSAGE
    assert "thebeakr.com" not in credentials.DEFAULT_BASE_URL
    assert credentials.configured_base_url() is None


@respx.mock(assert_all_mocked=True)
def test_no_url_configured_refuses_before_any_request() -> None:
    # Any request at all (to any host) would fail the test: nothing is mocked.
    with pytest.raises(AuthenticationError) as raised:
        Beakr(api_key="bk_live_secret")
    assert raised.value.message == MESSAGE
    assert raised.value.code == "base_url_required"
    assert "bk_live_secret" not in str(raised.value)
    assert not respx.calls


def test_async_client_refuses_too() -> None:
    with pytest.raises(AuthenticationError, match="Set your deployment's URL"):
        AsyncBeakr(api_key="bk_live_secret")


@respx.mock
def test_explicit_base_url_still_works() -> None:
    route = respx.get("https://beakr.example.com/v1/projects").respond(
        200, json={"data": [], "next_cursor": None, "has_more": False}
    )
    with Beakr(api_key="bk_live_k", base_url="https://beakr.example.com/") as beakr:
        assert beakr.base_url == "https://beakr.example.com"
        beakr.api.projects.list_projects()
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer bk_live_k"


@respx.mock
def test_beakr_base_url_env_still_works(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_BASE_URL", "https://env.example.com")
    route = respx.get("https://env.example.com/v1/projects").respond(
        200, json={"data": [], "next_cursor": None, "has_more": False}
    )
    with Beakr(api_key="bk_live_k") as beakr:
        assert beakr.base_url == "https://env.example.com"
        beakr.api.projects.list_projects()
    assert route.called


def test_cli_config_url_still_works() -> None:
    home = beakr_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"api_url": "https://cli.example.com/"}))
    with Beakr(api_key="bk_live_k") as beakr:
        assert beakr.base_url == "https://cli.example.com"


def test_a_legacy_saved_key_without_its_url_is_not_offered_anywhere() -> None:
    # An older CLI's config key with no api_url meant the hosted API; it is not
    # sent to whatever API is configured now.
    home = beakr_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"api_key": "bk_live_old"}))
    beakr = Beakr(base_url="https://beakr.example.com")
    with pytest.raises(AuthenticationError, match="No Beakr credentials found"):
        _ = beakr.credential


@respx.mock
def test_401_names_the_api_it_called() -> None:
    respx.get("https://beakr.example.com/v1/projects").respond(
        401, json={"error": {"code": "unauthorized", "message": "Invalid or expired API key"}}
    )
    with Beakr(api_key="bk_live_k", base_url="https://beakr.example.com", max_retries=0) as b:
        with pytest.raises(AuthenticationError) as raised:
            b.api.projects.list_projects()
    assert raised.value.message == ("Invalid or expired API key (called https://beakr.example.com)")


@respx.mock
def test_html_answer_does_not_suggest_the_hosted_url() -> None:
    respx.get("https://beakr.example.com/v1/projects").respond(
        200, text="<html><title>Sign in</title></html>", headers={"content-type": "text/html"}
    )
    with Beakr(api_key="bk_live_k", base_url="https://beakr.example.com", max_retries=0) as b:
        with pytest.raises(UnexpectedResponseError) as raised:
            b.api.projects.list_projects()
    assert "thebeakr.com" not in str(raised.value)
    assert "your deployment's API URL" in str(raised.value)


def test_no_runtime_source_names_the_hosted_api() -> None:
    root = Path(beakr_sdk.__file__).parent
    hits = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "api.thebeakr.com" in path.read_text()
    ]
    assert hits == []
