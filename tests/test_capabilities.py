"""``meta.capabilities()``: which documented operations a server serves.

Root cause guarded: a self-hosted installation serves only part of the
documented contract and answers a bare 404 for the rest, so a client offered
organization, integration and embedded-mode commands that could only fail
with "Not Found". The server's own public OpenAPI document lists what it
serves; these tests pin how the SDK reads and caches it.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr, Capabilities
from beakr_sdk.capabilities import CAPABILITIES_TTL_SECONDS, capabilities_flow, forget_capabilities

from .conftest import BASE

DOC = f"{BASE}/v1/public/openapi.json"

#: A box's document: projects and the contract document, no integrations or org admin.
BOX_DOCUMENT = {
    "openapi": "3.1.0",
    "paths": {
        "/v1/projects": {"get": {}, "post": {}},
        "/v1/projects/{project_id}": {"get": {}, "patch": {}, "delete": {}},
        "/v1/public/openapi.json": {"get": {}},
    },
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    forget_capabilities()
    yield
    forget_capabilities()


def test_a_documented_operation_missing_from_the_server_is_not_served() -> None:
    caps = Capabilities(BOX_DOCUMENT)
    assert caps.documented
    assert caps.serves("GET", "/v1/projects")
    assert caps.serves("GET", "/v1/projects/9f1c2a7e-0000-4000-8000-000000000000")
    assert caps.serves("get", "/v1/projects/?limit=5")
    assert not caps.serves("GET", "/v1/integrations")
    assert not caps.serves("POST", "/v1/organizations/{org_id}/members")
    assert not caps.serves("GET", "/v1/organizations/o1/external-users/u1")
    # A method the server does not serve on a path it does.
    assert not caps.serves("PUT", "/v1/projects/p1/knowledge-profile")
    assert caps.unserved([("GET", "/v1/projects"), ("GET", "/v1/integrations")]) == [
        ("GET", "/v1/integrations")
    ]


def test_a_route_outside_the_contract_is_assumed_served() -> None:
    # The document describes only the documented contract, so it cannot say
    # anything about another route.
    assert Capabilities(BOX_DOCUMENT).serves("GET", "/v1/some/unlisted/route")


def test_a_server_without_a_document_serves_everything() -> None:
    for caps in (Capabilities.unknown(), Capabilities({"openapi": "3.1.0"})):
        assert not caps.documented
        assert caps.serves("GET", "/v1/integrations")


@respx.mock
def test_the_document_is_fetched_once_per_base_url(client) -> None:
    route = respx.get(DOC).respond(200, json=BOX_DOCUMENT)
    first = client.meta.capabilities()
    second = client.meta.capabilities()
    assert route.call_count == 1
    assert first is second
    assert not first.serves("GET", "/v1/integrations")
    assert "authorization" not in route.calls.last.request.headers

    other = Beakr(api_key="bk_live_test", base_url="https://box.test")
    respx.get("https://box.test/v1/public/openapi.json").respond(200, json={"paths": {}})
    assert other.meta.capabilities().serves("GET", "/v1/integrations")
    other.close()

    client.meta.capabilities(refresh=True)
    assert route.call_count == 2


@respx.mock
def test_a_server_without_the_document_is_cached_as_serving_everything(client) -> None:
    route = respx.get(DOC).respond(404, json={"detail": "Not Found"})
    assert client.meta.capabilities().serves("GET", "/v1/integrations")
    assert client.meta.capabilities().serves("GET", "/v1/integrations")
    assert route.call_count == 1


@respx.mock
def test_an_unreachable_server_serves_everything(client) -> None:
    respx.get(DOC).mock(side_effect=httpx.ConnectError("refused"))
    assert client.meta.capabilities().serves("GET", "/v1/integrations")


def test_the_cache_expires_after_its_ttl() -> None:
    now = [100.0]

    def run(flow):
        try:
            next(flow)
        except StopIteration as stop:
            return stop.value, False
        try:
            flow.send(BOX_DOCUMENT)
        except StopIteration as stop:
            return stop.value, True
        raise AssertionError("the flow asks for one call at most")

    _, fetched = run(capabilities_flow(BASE, clock=lambda: now[0]))
    assert fetched
    now[0] += CAPABILITIES_TTL_SECONDS - 1
    _, fetched = run(capabilities_flow(BASE, clock=lambda: now[0]))
    assert not fetched
    now[0] += 2
    _, fetched = run(capabilities_flow(BASE, clock=lambda: now[0]))
    assert fetched


@respx.mock
async def test_the_async_client_reads_the_same_document() -> None:
    respx.get(DOC).respond(200, json=BOX_DOCUMENT)
    async with AsyncBeakr(api_key="bk_live_test", base_url=BASE) as beakr:
        caps = await beakr.meta.capabilities()
    assert caps.documented and not caps.serves("GET", "/v1/auth/end-user-tokens")
