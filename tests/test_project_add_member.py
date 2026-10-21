"""``projects.add_member`` sends a role only when one is given.

The engine keeps an existing member's role when the request names none (a new
member joins as ``member``); always sending ``role="member"`` demoted an owner
or admin who was added again, and the last owner answered ``409 last_owner``.
"""

from __future__ import annotations

import json

import respx

from .conftest import BASE

URL = f"{BASE}/v1/projects/p1/members"
MEMBER = {"principal_id": "u1", "principal_type": "user", "role": "admin"}


@respx.mock
def test_no_role_is_sent_by_default(client) -> None:
    route = respx.post(URL).respond(201, json=MEMBER)
    client.projects.add_member("p1", "u1")
    assert json.loads(route.calls.last.request.content) == {
        "principal_id": "u1",
        "principal_type": "user",
    }


@respx.mock
def test_a_given_role_is_sent(client) -> None:
    route = respx.post(URL).respond(201, json=MEMBER)
    client.projects.add_member("p1", "u1", role="viewer", principal_type="group")
    assert json.loads(route.calls.last.request.content) == {
        "principal_id": "u1",
        "principal_type": "group",
        "role": "viewer",
    }


@respx.mock
async def test_the_async_client_sends_a_role_only_when_given(async_client) -> None:
    route = respx.post(URL).respond(201, json=MEMBER)
    await async_client.projects.add_member("p1", "u1")
    assert "role" not in json.loads(route.calls.last.request.content)
    await async_client.projects.add_member("p1", "u1", role="owner")
    assert json.loads(route.calls.last.request.content)["role"] == "owner"
