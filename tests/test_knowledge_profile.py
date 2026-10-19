"""A project's Knowledge profile: read it, set it whole, change some fields.

``projects.knowledge_profile`` / ``set_knowledge_profile`` /
``update_knowledge_profile`` over ``/v1/projects/{project_id}/knowledge-profile``,
on both clients.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_sdk.errors import NotFoundError

from .conftest import BASE

URL = f"{BASE}/v1/projects/p1/knowledge-profile"
PROFILE = {
    "project_id": "p1",
    "name": "Basel",
    "description": "Batch records for the Basel site",
    "industry": "",
    "goals": ["Track deviations"],
    "focus_areas": [],
    "core_people": [{"name": "Priya", "role": "Owner", "relevance": ""}],
    "core_organizations": [],
    "core_topics": [],
    "entity_types": ["batch", "deviation"],
    "extraction_guidance": "",
    "created_at": "2026-10-19T00:00:00Z",
    "updated_at": "2026-10-19T00:00:00Z",
}


@respx.mock
def test_knowledge_profile_reads_the_project_profile(client) -> None:
    respx.get(URL).respond(200, json=PROFILE)
    assert client.projects.knowledge_profile("p1")["entity_types"] == ["batch", "deviation"]


@respx.mock
def test_a_project_without_a_profile_raises_not_found_with_its_code(client) -> None:
    respx.get(URL).respond(
        404,
        json={"error": {"code": "knowledge_profile_not_found", "message": "No profile yet."}},
    )
    with pytest.raises(NotFoundError) as caught:
        client.projects.knowledge_profile("p1")
    assert caught.value.code == "knowledge_profile_not_found"


@respx.mock
def test_set_knowledge_profile_puts_only_the_fields_given(client) -> None:
    route = respx.put(URL).respond(201, json=PROFILE)
    client.projects.set_knowledge_profile(
        "p1",
        description="Batch records for the Basel site",
        entity_types=["batch", "deviation"],
        core_people=[{"name": "Priya", "role": "Owner"}],
    )
    assert json.loads(route.calls.last.request.content) == {
        "description": "Batch records for the Basel site",
        "entity_types": ["batch", "deviation"],
        "core_people": [{"name": "Priya", "role": "Owner"}],
    }


@respx.mock
def test_update_knowledge_profile_patches_and_refuses_unknown_fields(client) -> None:
    route = respx.patch(URL).respond(200, json=PROFILE)
    client.projects.update_knowledge_profile("p1", entity_types=["batch"])
    assert json.loads(route.calls.last.request.content) == {"entity_types": ["batch"]}
    with pytest.raises(TypeError, match="entity_type"):
        client.projects.update_knowledge_profile("p1", entity_type=["batch"])
    assert route.call_count == 1


@respx.mock
async def test_async_knowledge_profile_round_trip(async_client) -> None:
    respx.put(URL).respond(201, json=PROFILE)
    respx.get(URL).respond(200, json=PROFILE)
    await async_client.projects.set_knowledge_profile("p1", name="Basel")
    profile = await async_client.projects.knowledge_profile("p1")
    assert profile["name"] == "Basel"
