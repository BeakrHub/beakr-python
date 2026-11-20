"""A project's card types: list them, change one, remove a scope's own copy, and
carry types to another project or up to the organization.

``knowledge.list_card_types`` / ``update_card_type`` / ``remove_card_type_copy`` /
``copy_card_types`` / ``promote_card_types`` over ``/v1/knowledge/wiki/page-types``,
on both clients.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_sdk.errors import ConflictError

from .conftest import BASE

LIST_URL = f"{BASE}/v1/knowledge/wiki/page-types"
TYPE_URL = f"{LIST_URL}/deviation"
PAGE_TYPE = {
    "key": "deviation",
    "label": "Deviation",
    "scope": "project",
    "status": "active",
    "overrides": "global",
    "metadata_schema": {
        "key_attributes": ["Root cause", "CAPA"],
        "required_attributes": ["Root cause"],
        "grain": "card",
    },
}


@respx.mock
def test_list_card_types_sends_only_the_filters_given(client) -> None:
    route = respx.get(LIST_URL).respond(200, json={"page_types": [PAGE_TYPE], "map_mode": True})
    listed = client.knowledge.list_card_types(project_id="p1", include_deprecated=True)
    assert listed["page_types"][0]["key"] == "deviation"
    assert dict(route.calls.last.request.url.params) == {
        "project_id": "p1",
        "include_deprecated": "true",
    }


@respx.mock
def test_update_card_type_patches_the_scope_and_only_the_fields_given(client) -> None:
    route = respx.patch(TYPE_URL).respond(200, json={"page_type": PAGE_TYPE})
    saved = client.knowledge.update_card_type(
        "deviation",
        project_id="p1",
        description="One departure from a batch record",
        fields=[{"name": "Root cause", "required": True}, {"name": "CAPA"}],
    )
    assert saved["page_type"]["overrides"] == "global"
    assert json.loads(route.calls.last.request.content) == {
        "scope": "project",
        "project_id": "p1",
        "description": "One departure from a batch record",
        "fields": [{"name": "Root cause", "required": True}, {"name": "CAPA"}],
    }


@respx.mock
def test_an_organization_wide_change_carries_no_project(client) -> None:
    route = respx.patch(TYPE_URL).respond(200, json={"page_type": {**PAGE_TYPE, "scope": "org"}})
    client.knowledge.update_card_type("deviation", scope="org", status="deprecated", aliases=[])
    assert json.loads(route.calls.last.request.content) == {
        "scope": "org",
        "status": "deprecated",
        "aliases": [],
    }


@respx.mock
def test_remove_card_type_copy_deletes_the_scope_copy(client) -> None:
    route = respx.delete(TYPE_URL).respond(
        200, json={"removed": True, "key": "deviation", "scope": "project"}
    )
    removed = client.knowledge.remove_card_type_copy("deviation", project_id="p1")
    assert removed == {"removed": True, "key": "deviation", "scope": "project"}
    assert dict(route.calls.last.request.url.params) == {"scope": "project", "project_id": "p1"}


@respx.mock
def test_removing_a_type_defined_only_in_the_scope_raises_conflict(client) -> None:
    respx.delete(TYPE_URL).respond(
        409,
        json={"error": {"code": "conflict", "message": "This type is defined only here."}},
    )
    with pytest.raises(ConflictError):
        client.knowledge.remove_card_type_copy("deviation", scope="org")


@respx.mock
async def test_async_card_type_round_trip(async_client) -> None:
    respx.get(LIST_URL).respond(200, json={"page_types": [PAGE_TYPE], "map_mode": True})
    respx.patch(TYPE_URL).respond(200, json={"page_type": PAGE_TYPE})
    respx.delete(TYPE_URL).respond(
        200, json={"removed": True, "key": "deviation", "scope": "project"}
    )
    listed = await async_client.knowledge.list_card_types(project_id="p1")
    assert listed["map_mode"] is True
    saved = await async_client.knowledge.update_card_type("deviation", project_id="p1", grain="row")
    assert saved["page_type"]["key"] == "deviation"
    removed = await async_client.knowledge.remove_card_type_copy("deviation", project_id="p1")
    assert removed["removed"] is True


@respx.mock
def test_copy_card_types_names_both_projects_and_leaves_replace_to_the_server(client) -> None:
    copied = {"copied": [PAGE_TYPE], "replaced": [], "skipped": []}
    route = respx.post(f"{LIST_URL}/copy").respond(200, json=copied)
    assert (
        client.knowledge.copy_card_types(["deviation"], from_project_id="p1", to_project_id="p2")
        == copied
    )
    assert json.loads(route.calls.last.request.content) == {
        "from_project_id": "p1",
        "to_project_id": "p2",
        "keys": ["deviation"],
    }


@respx.mock
def test_promote_card_types_names_the_source_not_a_project_id(client) -> None:
    answer = {
        "copied": [],
        "replaced": [{**PAGE_TYPE, "scope": "org"}],
        "skipped": [{"key": "capa", "reason": "not_found"}],
    }
    route = respx.post(f"{LIST_URL}/promote").respond(200, json=answer)
    promoted = client.knowledge.promote_card_types(["deviation", "capa"], source="p1", replace=True)
    assert promoted["skipped"] == [{"key": "capa", "reason": "not_found"}]
    assert json.loads(route.calls.last.request.content) == {
        "source": "p1",
        "keys": ["deviation", "capa"],
        "replace": True,
    }


@respx.mock
async def test_async_copy_and_promote(async_client) -> None:
    answer = {"copied": [PAGE_TYPE], "replaced": [], "skipped": []}
    respx.post(f"{LIST_URL}/copy").respond(200, json=answer)
    respx.post(f"{LIST_URL}/promote").respond(200, json=answer)
    copied = await async_client.knowledge.copy_card_types(
        ["deviation"], from_project_id="p1", to_project_id="p2"
    )
    promoted = await async_client.knowledge.promote_card_types(["deviation"], source="p1")
    assert copied == promoted == answer
