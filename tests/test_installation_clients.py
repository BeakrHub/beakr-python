"""``meta.installation_clients()``: the client versions an installation's release ships.

A self-hosted installation serves ``/clients/manifest.json`` from its web image
(beakr-engine client_index.py). The SDK reads it without a credential, and
``check_installation_clients()`` warns once when this beakr-sdk is not the version
the release ships. Constructing a client asks for nothing.
"""

from __future__ import annotations

import warnings

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr, InstallationClients, InstallationClientsWarning
from beakr_sdk import __version__ as SDK_VERSION
from beakr_sdk.installation_clients import forget_installation_clients

from .conftest import BASE

MANIFEST_URL = f"{BASE}/clients/manifest.json"
SHIPPED = "9.9.9+selfhosted.gaaaaaaaaaaaa"


def manifest(sdk: str = SHIPPED, *, schema: int = 1) -> dict:
    return {
        "schema_version": schema,
        "release": "1.0.7",
        "python_index": "/pypi/simple/",
        "clients": {
            "beakr-cli": {"kind": "python", "version": "0.8.0+selfhosted.gb"},
            "beakr-sdk": {
                "kind": "python",
                "version": sdk,
                "files": [],
                "install": (
                    f'pip install --extra-index-url {{origin}}/pypi/simple "beakr-sdk=={sdk}"'
                ),
            },
            "@beakr/sdk": {"kind": "npm", "version": "0.5.0-selfhosted.gc"},
        },
    }


@pytest.fixture(autouse=True)
def _fresh_cache():
    forget_installation_clients()
    yield
    forget_installation_clients()


@respx.mock
def test_reads_the_manifest_without_a_credential(client) -> None:
    route = respx.get(MANIFEST_URL).respond(200, json=manifest())

    found = client.meta.installation_clients()

    assert isinstance(found, InstallationClients)
    assert found.release == "1.0.7"
    assert found.version("beakr-sdk") == SHIPPED
    assert found.version("beakr-cli") == "0.8.0+selfhosted.gb"
    assert found.version("@beakr/sdk") == "0.5.0-selfhosted.gc"
    assert found.packages == ["@beakr/sdk", "beakr-cli", "beakr-sdk"]
    assert found.install_command("beakr-sdk") == (
        f'pip install --extra-index-url {BASE}/pypi/simple "beakr-sdk=={SHIPPED}"'
    )
    assert found.sdk_matches is False
    assert "authorization" not in route.calls.last.request.headers
    # Kept for a few minutes per installation.
    assert client.meta.installation_clients() is found
    assert route.call_count == 1


@respx.mock
def test_constructing_a_client_asks_for_nothing() -> None:
    route = respx.get(MANIFEST_URL).respond(200, json=manifest())
    Beakr(api_key="bk_live_test", base_url=BASE).close()
    assert route.call_count == 0


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, json={"detail": "Not Found"}),
        httpx.Response(200, text="<html>sign in</html>", headers={"content-type": "text/html"}),
        httpx.Response(200, json=manifest(schema=2)),
        httpx.Response(200, json={"schema_version": 1, "release": "", "clients": {}}),
    ],
)
@respx.mock
def test_no_manifest_is_none(client, answer) -> None:
    respx.get(MANIFEST_URL).mock(return_value=answer)
    assert client.meta.installation_clients() is None


@respx.mock
def test_a_mismatch_warns_once(client) -> None:
    respx.get(MANIFEST_URL).respond(200, json=manifest())

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.meta.check_installation_clients()
        client.meta.check_installation_clients(refresh=True)

    relevant = [w for w in caught if issubclass(w.category, InstallationClientsWarning)]
    assert len(relevant) == 1
    assert str(relevant[0].message) == (
        f"This installation (release 1.0.7) ships beakr-sdk {SHIPPED}; you have {SDK_VERSION}. "
        f'Upgrade: pip install --extra-index-url {BASE}/pypi/simple "beakr-sdk=={SHIPPED}"'
    )


@respx.mock
def test_a_match_and_no_manifest_never_warn(client) -> None:
    respx.get(MANIFEST_URL).respond(200, json=manifest(SDK_VERSION))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        found = client.meta.check_installation_clients()
        assert found is not None and found.sdk_matches is True
        respx.get(MANIFEST_URL).respond(404)
        assert client.meta.check_installation_clients(refresh=True) is None


@respx.mock
async def test_the_async_client_reads_it_too(async_client) -> None:
    respx.get(MANIFEST_URL).respond(200, json=manifest())
    found = await async_client.meta.installation_clients()
    assert isinstance(found, InstallationClients) and found.release == "1.0.7"
    assert isinstance(async_client, AsyncBeakr)
