"""The connections a project's sources can be attached from, on both clients.

``integrations.list_for_project`` (``GET /v1/projects/{project_id}/integrations``),
which a key limited to projects may call where ``GET /v1/integrations`` is
refused. The item shape is the engine's ``IntegrationOut``.
"""

from __future__ import annotations

import respx

from .conftest import BASE

DRIVE = {
    "id": "c1",
    "provider": "gdrive:nango",
    "label": "Lab Drive",
    "scope": "org",
    "status": "active",
    "projects": [{"project_id": "p1", "attachment_id": "s1"}],
}


@respx.mock
def test_lists_the_projects_connections_with_paging(client) -> None:
    route = respx.get(f"{BASE}/v1/projects/p1/integrations").respond(
        200, json={"data": [DRIVE], "has_more": False, "next_cursor": None}
    )

    page = client.integrations.list_for_project("p1", limit=5)

    assert [row["id"] for row in page["data"]] == ["c1"]
    assert dict(route.calls.last.request.url.params) == {"limit": "5"}


@respx.mock
async def test_the_async_client_lists_them_too(async_client) -> None:
    respx.get(f"{BASE}/v1/projects/p1/integrations").respond(
        200, json={"data": [DRIVE], "has_more": False, "next_cursor": None}
    )
    page = await async_client.integrations.list_for_project("p1")
    assert page["data"][0]["provider"] == "gdrive:nango"
