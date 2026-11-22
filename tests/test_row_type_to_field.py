"""``knowledge.convert_row_type_to_field`` (``POST /v1/knowledge/wiki/page-types/{key}/to-field``).

Row types are removed (ENG-2471): an existing row type becomes a field of the type
its rows belong on. Shapes are the engine's ``WikiPageTypeToField`` and
``WikiPageTypeMadeField``.
"""

from __future__ import annotations

import json

import respx

from .conftest import BASE

TO_FIELD = f"{BASE}/v1/knowledge/wiki/page-types/assay_run/to-field"
MADE = {"page_type": {"key": "assay"}, "former_row_type": {"key": "assay_run"}}


@respx.mock
def test_it_sends_the_field_and_the_scope(client) -> None:
    route = respx.post(TO_FIELD).respond(200, json=MADE)
    answer = client.knowledge.convert_row_type_to_field(
        "assay_run", on_type="assay", name="Runs", description="one row per run", project_id="p1"
    )
    assert answer == MADE
    assert json.loads(route.calls.last.request.content) == {
        "on_type": "assay",
        "name": "Runs",
        "description": "one row per run",
        "scope": "project",
        "project_id": "p1",
    }


@respx.mock
def test_an_organization_change_names_no_project(client) -> None:
    route = respx.post(TO_FIELD).respond(200, json=MADE)
    client.knowledge.convert_row_type_to_field(
        "assay_run", on_type="assay", name="Runs", scope="org"
    )
    assert json.loads(route.calls.last.request.content) == {
        "on_type": "assay",
        "name": "Runs",
        "scope": "org",
    }


@respx.mock
async def test_async(async_client) -> None:
    respx.post(TO_FIELD).respond(200, json=MADE)
    assert (
        await async_client.knowledge.convert_row_type_to_field(
            "assay_run", on_type="assay", name="Runs", project_id="p1"
        )
        == MADE
    )
