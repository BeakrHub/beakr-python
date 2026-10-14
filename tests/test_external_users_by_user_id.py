"""External users are addressed by user_id, never by external_id alone.

Root cause: an external_id is unique only within the service account that
created the user, so two backends in one organization may each have a
"customer-42". A call that passed an external_id where a user is named could
reach another backend's user (or a confusing 404). get, update, delete and
token minting take the user_id and refuse an external_id before any request;
``list(external_id=...)`` stays the caller-scoped way to find it.
"""

from __future__ import annotations

import pytest
import respx

from beakr_sdk import Beakr

BASE = "https://api.test"
ORG = "org-1"
USER_ID = "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f"


@pytest.fixture
def beakr():
    with Beakr(api_key="bk_live_x", base_url=BASE, org_id=ORG) as client:
        yield client


@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.external_users.get("customer-42"),
        lambda b: b.external_users.update("customer-42", name="Ada"),
        lambda b: b.external_users.delete("customer-42"),
        lambda b: b.end_user_tokens.mint(scopes=["kb:read"], external_user_id="customer-42"),
    ],
)
def test_an_external_id_is_refused_before_any_request(beakr, call):
    with respx.mock(assert_all_called=False) as mock:
        anything = mock.route().respond(200, json={})
        with pytest.raises(ValueError, match="external_id='customer-42'"):
            call(beakr)
    assert not anything.called


@respx.mock
def test_a_user_id_reaches_the_user(beakr):
    route = respx.get(f"{BASE}/v1/organizations/{ORG}/external-users/{USER_ID}").respond(
        200, json={"user_id": USER_ID}
    )
    assert beakr.external_users.get(USER_ID)["user_id"] == USER_ID
    assert route.called
