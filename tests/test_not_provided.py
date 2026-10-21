"""A route this installation does not have raises ``NotProvidedError``.

A self-hosted Beakr leaves out hosted areas (skills, integrations, organization
administration and members, external users, end-user tokens, service
accounts) and answers them with the router's bare ``{"detail": "Not Found"}``.
That used to surface as ``NotFoundError: Not Found (HTTP 404)``, which reads
like a bug. A missing record answers 404 with an error envelope and stays a
plain ``NotFoundError``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_sdk import NotFoundError, NotProvidedError
from beakr_sdk.errors import error_from_response

from .conftest import BASE

BARE = {"detail": "Not Found"}


@pytest.mark.parametrize(
    ("call", "area"),
    [
        (lambda c: c.skills.list(), "skills"),
        (lambda c: c.skills.proposals.list(), "skills"),
        (lambda c: c.integrations.list(), "integrations"),
        (lambda c: c.members.list(org_id="o1"), "organization members"),
        (lambda c: c.organizations.list(), "organization administration"),
        (lambda c: c.external_users.list(org_id="o1"), "external users"),
        (lambda c: c.end_user_tokens.list(), "end-user tokens"),
        (lambda c: c.org.service_accounts.list(org_id="o1"), "service accounts"),
    ],
)
@respx.mock
def test_a_bare_404_names_the_area_the_installation_leaves_out(client, call, area) -> None:
    respx.route(host="api.test").respond(404, json=BARE)
    with pytest.raises(NotProvidedError) as caught:
        call(client)
    error = caught.value
    assert error.message == f"This Beakr installation does not provide {area}."
    assert error.area == area
    assert str(error).startswith(f"This Beakr installation does not provide {area}. (HTTP 404")
    # Existing ``except NotFoundError`` handlers keep catching it.
    assert isinstance(error, NotFoundError)
    assert error.status == 404
    assert error.code == "not_found"


@respx.mock
def test_a_bare_404_outside_a_known_area_names_the_operation(client) -> None:
    respx.get(f"{BASE}/v1/me/organization").respond(404, json=BARE)
    with pytest.raises(NotProvidedError) as caught:
        client.me.active_org()
    error = caught.value
    assert error.message == "This Beakr installation does not provide GET /v1/me/organization."
    assert error.area is None
    assert (error.method, error.path) == ("GET", "/v1/me/organization")


@respx.mock
async def test_the_async_client_raises_it_too(async_client) -> None:
    respx.get(f"{BASE}/v1/integrations").respond(404, json=BARE)
    with pytest.raises(NotProvidedError, match="does not provide integrations"):
        await async_client.integrations.list()


@respx.mock
def test_a_missing_record_stays_a_plain_not_found(client) -> None:
    respx.get(f"{BASE}/v1/skills/s1").respond(
        404,
        json={
            "detail": "Not Found",
            "error": {"code": "not_found", "message": "Not Found", "request_id": "r1"},
        },
    )
    with pytest.raises(NotFoundError) as caught:
        client.skills.get("s1")
    assert type(caught.value) is NotFoundError
    assert caught.value.message == "Not Found"


@respx.mock
def test_a_legacy_404_with_its_own_detail_stays_a_plain_not_found(client) -> None:
    respx.get(f"{BASE}/v1/projects/p1").respond(404, json={"detail": "Project not found"})
    with pytest.raises(NotFoundError) as caught:
        client.projects.get("p1")
    assert type(caught.value) is NotFoundError


def test_a_base_url_prefix_is_ignored_when_naming_the_area() -> None:
    error = error_from_response(
        httpx.Response(
            404, json=BARE, request=httpx.Request("GET", "https://box.test/api/v1/skills")
        )
    )
    assert isinstance(error, NotProvidedError)
    assert error.area == "skills"


def test_a_response_without_its_request_still_raises_not_provided() -> None:
    error = error_from_response(httpx.Response(404, json=BARE))
    assert isinstance(error, NotProvidedError)
    assert error.message == "This Beakr installation does not provide this operation."
