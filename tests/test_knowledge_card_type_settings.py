"""A project's card type settings, the history of type changes, and suggestions.

``knowledge.card_type_settings`` / ``set_card_type_settings`` (a dry run unless
asked to write), ``card_type_history`` / ``iter_card_type_history``,
``card_type_suggestions`` / ``dismiss_card_type_suggestion``, ``card_types`` on
``projects.create`` and ``suggestion_id`` on ``retype_cards``, on both clients.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_sdk.errors import ConflictError

from .conftest import BASE

TYPES = f"{BASE}/v1/knowledge/wiki/page-types"
SETTINGS = {"inherit": "organization", "agent_may_change": True}
CHANGE = {
    "project_id": "p1",
    "dry_run": True,
    "current": SETTINGS,
    "requested": {"inherit": "own", "agent_may_change": True},
    "kept_types": ["index", "organization", "overview", "person", "topic"],
    "types_dropped": [{"key": "matter", "label": "Matter", "scope": "global", "live_cards": 4}],
    "types_kept": [],
    "usable_own_types": ["deviation"],
    "suggestions": 0,
    "history_id": None,
    "undo_event_id": None,
}
ENTRY = {
    "id": "h1",
    "actor_type": "user",
    "scope": "project",
    "key": "deviation",
    "label": "Deviation",
    "kind": "edited",
    "title": "Changed Deviation",
    "changed": ["description"],
    "undoable": True,
    "details": {},
}


@respx.mock
def test_card_type_settings_reads_them_from_the_type_list(client) -> None:
    route = respx.get(TYPES).respond(
        200, json={"page_types": [], "map_mode": True, "settings": SETTINGS, "kept_types": []}
    )
    assert client.knowledge.card_type_settings("p1") == SETTINGS
    assert dict(route.calls.last.request.url.params) == {"project_id": "p1"}


@respx.mock
def test_list_card_types_can_ask_for_card_counts(client) -> None:
    route = respx.get(TYPES).respond(
        200,
        json={
            "page_types": [{"key": "deviation", "card_count": 7}],
            "map_mode": True,
            "unlisted_types": [{"key": "matter", "card_count": 2}],
        },
    )
    listed = client.knowledge.list_card_types(project_id="p1", include_counts=True)
    assert listed["unlisted_types"] == [{"key": "matter", "card_count": 2}]
    assert dict(route.calls.last.request.url.params) == {
        "project_id": "p1",
        "include_counts": "true",
    }


@respx.mock
def test_set_card_type_settings_is_a_dry_run_unless_asked_to_write(client) -> None:
    # Root cause guarded: applying changes which types a whole project is
    # offered, so dry_run false is sent only when the caller says so.
    route = respx.put(f"{TYPES}/settings").respond(200, json=CHANGE)
    preview = client.knowledge.set_card_type_settings("p1", inherit="own")
    assert preview["types_dropped"][0]["live_cards"] == 4
    assert json.loads(route.calls.last.request.content) == {
        "project_id": "p1",
        "inherit": "own",
        "keep_types_in_use": False,
        "dry_run": True,
    }
    client.knowledge.set_card_type_settings(
        "p1", agent_may_change=False, keep_types_in_use=True, dry_run=False
    )
    assert json.loads(route.calls.last.request.content) == {
        "project_id": "p1",
        "agent_may_change": False,
        "keep_types_in_use": True,
        "dry_run": False,
    }


@respx.mock
def test_card_type_history_is_one_page_and_iter_follows_the_cursor(client) -> None:
    route = respx.get(f"{TYPES}/history")
    route.side_effect = [
        respx.MockResponse(200, json={"data": [ENTRY], "has_more": True, "next_cursor": "c2"}),
        respx.MockResponse(200, json={"data": [ENTRY], "has_more": True, "next_cursor": "c2"}),
        respx.MockResponse(
            200, json={"data": [{**ENTRY, "id": "h2"}], "has_more": False, "next_cursor": None}
        ),
    ]
    page = client.knowledge.card_type_history(scope="org", key="deviation", limit=1)
    assert page["next_cursor"] == "c2"
    assert dict(route.calls.last.request.url.params) == {
        "scope": "org",
        "key": "deviation",
        "limit": "1",
    }
    entries = list(
        client.knowledge.iter_card_type_history(project_id="p1", include_suggestions=True)
    )
    assert [entry["id"] for entry in entries] == ["h1", "h2"]
    assert route.calls.last.request.url.params["cursor"] == "c2"
    assert route.calls.last.request.url.params["include_suggestions"] == "true"


@respx.mock
def test_suggestions_are_listed_and_dismissed_for_their_project(client) -> None:
    suggestion = {
        **ENTRY,
        "id": "sg1",
        "kind": "retype_suggested",
        "state": "pending",
        "details": {"from_type": "matter", "cards": 4},
    }
    listing = respx.get(f"{TYPES}/suggestions").respond(
        200, json={"data": [suggestion], "has_more": False, "next_cursor": None}
    )
    dismiss = respx.post(f"{TYPES}/suggestions/sg1/dismiss").respond(
        200, json={"suggestion": {**suggestion, "state": "dismissed"}, "activity_event_id": "e1"}
    )
    assert client.knowledge.card_type_suggestions("p1")["data"][0]["details"]["cards"] == 4
    assert dict(listing.calls.last.request.url.params) == {"project_id": "p1"}
    assert [s["id"] for s in client.knowledge.iter_card_type_suggestions("p1")] == ["sg1"]
    dismissed = client.knowledge.dismiss_card_type_suggestion("sg1", "p1")
    assert dismissed["suggestion"]["state"] == "dismissed"
    assert json.loads(dismiss.calls.last.request.content) == {"project_id": "p1"}


@respx.mock
def test_dismissing_an_answered_suggestion_raises_conflict(client) -> None:
    respx.post(f"{TYPES}/suggestions/sg1/dismiss").respond(
        409, json={"error": {"code": "conflict", "message": "Already answered."}}
    )
    with pytest.raises(ConflictError):
        client.knowledge.dismiss_card_type_suggestion("sg1", "p1")


@respx.mock
def test_retype_cards_passes_the_suggestion_it_answers(client) -> None:
    route = respx.post(f"{TYPES}/matter/retype-cards").respond(200, json={"cards": 4})
    client.knowledge.retype_cards("matter", "p1", "deviation", suggestion_id="sg1")
    assert json.loads(route.calls.last.request.content) == {
        "project_id": "p1",
        "to_type": "deviation",
        "dry_run": True,
        "suggestion_id": "sg1",
    }
    client.knowledge.retype_cards("matter", "p1", "deviation")
    assert "suggestion_id" not in json.loads(route.calls.last.request.content)


@respx.mock
def test_a_project_is_created_with_its_card_type_choice(client) -> None:
    route = respx.post(f"{BASE}/v1/projects").respond(201, json={"id": "p9", "name": "Lyon"})
    client.projects.create(
        "Lyon",
        card_types={"inherit": "own", "copy_from_project_id": "p1", "copy_keys": ["deviation"]},
    )
    assert json.loads(route.calls.last.request.content) == {
        "name": "Lyon",
        "card_types": {
            "inherit": "own",
            "copy_from_project_id": "p1",
            "copy_keys": ["deviation"],
        },
    }
    client.projects.create("Basel")
    assert json.loads(route.calls.last.request.content) == {"name": "Basel"}


@respx.mock
async def test_async_settings_history_and_suggestions(async_client) -> None:
    respx.get(TYPES).respond(200, json={"page_types": [], "map_mode": True, "settings": SETTINGS})
    put = respx.put(f"{TYPES}/settings").respond(200, json=CHANGE)
    respx.get(f"{TYPES}/history").respond(
        200, json={"data": [ENTRY], "has_more": False, "next_cursor": None}
    )
    respx.get(f"{TYPES}/suggestions").respond(
        200, json={"data": [], "has_more": False, "next_cursor": None}
    )
    assert await async_client.knowledge.card_type_settings("p1") == SETTINGS
    await async_client.knowledge.set_card_type_settings("p1", inherit="builtin")
    assert json.loads(put.calls.last.request.content)["dry_run"] is True
    page = await async_client.knowledge.card_type_history(project_id="p1")
    assert page["data"][0]["id"] == "h1"
    seen = [e async for e in async_client.knowledge.iter_card_type_history(project_id="p1")]
    assert [e["id"] for e in seen] == ["h1"]
    assert (await async_client.knowledge.card_type_suggestions("p1"))["data"] == []
