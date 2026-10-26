"""``knowledge.proposals.create`` only sends what the proposals route accepts.

Root cause (sandbox e2e, 2026-10-25): the docstring offered the write door's
actions (``new``) and ``scope=``, but the route takes ``create|edit|find_replace|
mv|archive|copy`` and a required ``project_id``. ``new`` came back as a 422 that
asked for ``sections.0.id`` and ``scope`` was dropped, so one page took three
round trips. The SDK now refuses those before sending.
"""

from __future__ import annotations

import json

import pytest
import respx

from .conftest import BASE


@respx.mock
def test_create_sends_the_action_and_project_id(client) -> None:
    route = respx.post(f"{BASE}/v1/knowledge/wiki/proposals").respond(
        200, json={"id": "wp1", "proposal_type": "create"}
    )
    client.knowledge.proposals.create(
        "create", project_id="p1", title="Plate 3 rerun", content="# Plate 3\n"
    )
    assert json.loads(route.calls.last.request.content) == {
        "action": "create",
        "project_id": "p1",
        "title": "Plate 3 rerun",
        "content": "# Plate 3\n",
    }


@respx.mock
def test_a_write_door_action_is_refused_before_sending(client) -> None:
    route = respx.post(f"{BASE}/v1/knowledge/wiki/proposals")
    with pytest.raises(ValueError, match=r"knowledge\.write\('new'"):
        client.knowledge.proposals.create("new", project_id="p1", title="x", content="y")
    assert not route.called


@respx.mock
def test_scope_is_refused_rather_than_dropped(client) -> None:
    route = respx.post(f"{BASE}/v1/knowledge/wiki/proposals")
    with pytest.raises(TypeError, match="project_id"):
        client.knowledge.proposals.create("create", scope="Research", title="x", content="y")
    assert not route.called


@respx.mock
def test_project_id_is_required(client) -> None:
    route = respx.post(f"{BASE}/v1/knowledge/wiki/proposals")
    with pytest.raises(ValueError, match="project_id"):
        client.knowledge.proposals.create("archive", page="Old")
    assert not route.called
