"""Behaviour of the C039 parity methods beyond the request shape the coverage test checks.

``test_contract_coverage.py`` proves every contract operation has a method that
sends it; these tests pin the bodies, defaults and multi-request flows.
"""

from __future__ import annotations

import json

import httpx
import respx

from .conftest import BASE


@respx.mock
def test_members_add_resolves_the_active_org(client) -> None:
    respx.get(f"{BASE}/v1/me/organization").respond(200, json={"id": "org1"})
    route = respx.post(f"{BASE}/v1/organizations/org1/members").respond(201, json={})
    client.members.add("u1", role="admin")
    assert json.loads(route.calls.last.request.content) == {"user_id": "u1", "role": "admin"}


@respx.mock
def test_meta_openapi_is_unauthenticated(client) -> None:
    route = respx.get(f"{BASE}/v1/public/openapi.json").mock(
        return_value=httpx.Response(200, json={"openapi": "3.1.0"})
    )
    client.meta.openapi()
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_files_read_sends_the_reference_with_its_unit(client) -> None:
    # files.read is POST /v1/artifacts/read: one call takes a path, token or id,
    # with the place in the file after `#` (what a Knowledge Sources line prints).
    route = respx.post(f"{BASE}/v1/artifacts/read").respond(
        200,
        json={
            "file": {
                "id": "f1",
                "version_id": "v1",
                "title": "memo.pdf",
                "display_path": "d/memo.pdf",
            },
            "unit": {"locator": "p. 7", "found": True, "page": 7, "page_count": 9},
            "text": "Rent is $5",
            "start": 0,
            "end": 10,
            "has_more": True,
            "next_reference": "d/memo.pdf#p. 8",
        },
    )
    window = client.files.read("d/memo.pdf", unit="p. 7")
    assert json.loads(route.calls.last.request.content) == {
        "reference": "d/memo.pdf#p. 7",
        "offset": 0,
        "max_chars": 20000,
    }
    assert window["next_reference"] == "d/memo.pdf#p. 8"
    client.files.read("{{artifact:v1-u3}}", offset=40, max_chars=100)
    assert json.loads(route.calls.last.request.content) == {
        "reference": "{{artifact:v1-u3}}",
        "offset": 40,
        "max_chars": 100,
    }
