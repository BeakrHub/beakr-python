"""Resolving a review flag: ``knowledge.proposals.conflict``, ``resolution_options``,
``resolve``, ``resolve_section``, ``resolve_rows`` and ``resolve_on_every_card``.

``resolve`` applies a drafted option by the route that fits it (a card-table
option with ``POST .../resolve-rows``, any other as a section rewrite with
``POST .../resolve``), pinned to the revision the options were drafted against.
It reads the page's revision first, so a page that changed is refused before
anything is drafted again or applied. Shapes are the engine's
``WikiConflictDetail``, ``WikiConflictResolutionOptions``, ``WikiConflictResolved``
and ``WikiConflictCardsResolved``.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_sdk.errors import ConflictError, PersonReviewRequiredError
from beakr_sdk.resources.knowledge import RESOLUTIONS_TIMEOUT, find_resolution_option

from .conftest import BASE

FLAG = f"{BASE}/v1/knowledge/wiki/proposals/f1"
SECTION_OPTION = {
    "id": "A",
    "label": "Keep 12nM",
    "summary": "Keep Lee 2023.",
    "new_section_body": "<!-- sec:dosing -->\n## Dosing\nIC50 is 12nM (Lee 2023).",
}
ROW_OPTION = {
    "id": "B",
    "label": "Retire the 40nM row",
    "summary": "Delete the disputing row.",
    "new_section_body": "<!-- sec:facts -->\n| ... |",
    "row_operation": {
        "table": "facts",
        "kind": "delete",
        "row_ids": ["r-2"],
        "row": None,
        "reason": "A different assay.",
    },
}
RESOLVED = {
    "status": "resolved",
    "proposal_id": "f1",
    "page_id": "p1",
    "section_id": "dosing",
    "new_revision": 4,
}


def _conflict(revision: int = 3) -> dict:
    return {
        "proposal_id": "f1",
        "page_id": "p1",
        "page_title": "Compound X",
        "section_id": "dosing",
        "section_content": "## Dosing\nIC50 is 12nM.",
        "reason": "Sources disagree on the IC50.",
        "current_revision": revision,
    }


def _drafts(revision: int = 3) -> dict:
    return {
        "proposal_id": "f1",
        "section_id": "dosing",
        "current_revision": revision,
        "options": [SECTION_OPTION, ROW_OPTION],
        "cached": True,
        "no_draft_reason": None,
    }


def _serve(revision: int = 3, drafted_revision: int | None = None):
    routes = {
        "conflict": respx.get(f"{FLAG}/conflict").respond(200, json=_conflict(revision)),
        "drafts": respx.post(f"{FLAG}/resolutions").respond(
            200, json=_drafts(revision if drafted_revision is None else drafted_revision)
        ),
        "resolve": respx.post(f"{FLAG}/resolve").respond(200, json=RESOLVED),
        "rows": respx.post(f"{FLAG}/resolve-rows").respond(200, json=RESOLVED),
    }
    return routes


def _body(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_conflict_and_drafts_read_the_flag_and_wait_for_the_model(client) -> None:
    routes = _serve()
    assert client.knowledge.proposals.conflict("f1")["current_revision"] == 3
    drafted = client.knowledge.proposals.resolution_options("f1")
    assert [option["id"] for option in drafted["options"]] == ["A", "B"]
    timeout = routes["drafts"].calls.last.request.extensions["timeout"]["read"]
    assert timeout == RESOLUTIONS_TIMEOUT
    assert RESOLUTIONS_TIMEOUT >= 120


@respx.mock
def test_a_section_option_is_applied_as_a_rewrite_pinned_to_the_revision(client) -> None:
    routes = _serve()
    assert client.knowledge.proposals.resolve("f1", option_id="a", expected_revision=3) == RESOLVED
    assert _body(routes["resolve"]) == {
        "section_id": "dosing",
        "new_section_body": SECTION_OPTION["new_section_body"],
        "expected_revision": 3,
        "option_id": "A",
    }
    assert not routes["rows"].called


@respx.mock
def test_a_row_option_is_applied_with_resolve_rows(client) -> None:
    routes = _serve()
    client.knowledge.proposals.resolve("f1", option_id="2", expected_revision=3)
    assert _body(routes["rows"]) == {"option_id": "B", "expected_revision": 3}
    assert not routes["resolve"].called


@respx.mock
def test_own_section_text_rewrites_the_flagged_section(client) -> None:
    routes = _serve()
    client.knowledge.proposals.resolve(
        "f1", section_body="## Dosing\n12-40nM.", expected_revision=3
    )
    assert _body(routes["resolve"]) == {
        "section_id": "dosing",
        "new_section_body": "## Dosing\n12-40nM.",
        "expected_revision": 3,
    }
    assert not routes["drafts"].called


@respx.mock
def test_a_page_that_changed_is_refused_before_anything_is_drafted_or_sent(client) -> None:
    routes = _serve(revision=5)
    with pytest.raises(ConflictError) as caught:
        client.knowledge.proposals.resolve("f1", option_id="A", expected_revision=3)
    assert "changed since revision 3 (now 5)" in caught.value.message
    assert not routes["drafts"].called
    assert not routes["resolve"].called and not routes["rows"].called


@respx.mock
def test_drafts_of_another_revision_are_never_applied(client) -> None:
    routes = _serve(revision=3, drafted_revision=4)
    with pytest.raises(ConflictError):
        client.knowledge.proposals.resolve("f1", option_id="A", expected_revision=3)
    assert not routes["resolve"].called and not routes["rows"].called


@respx.mock
def test_an_unknown_option_is_refused(client) -> None:
    routes = _serve()
    with pytest.raises(ValueError, match="not among"):
        client.knowledge.proposals.resolve("f1", option_id="Z", expected_revision=3)
    assert not routes["resolve"].called


def test_resolve_takes_exactly_one_resolution(client) -> None:
    with pytest.raises(ValueError):
        client.knowledge.proposals.resolve("f1", expected_revision=3)
    with pytest.raises(ValueError):
        client.knowledge.proposals.resolve(
            "f1", option_id="A", section_body="x", expected_revision=3
        )


@respx.mock
def test_a_stale_revision_from_the_server_is_a_conflict_error(client) -> None:
    respx.post(f"{FLAG}/resolve").respond(
        409,
        json={
            "detail": "The page changed since you opened this conflict.",
            "error": {"code": "conflict", "message": "The page changed since you opened this."},
        },
    )
    with pytest.raises(ConflictError):
        client.knowledge.proposals.resolve_section(
            "f1", section_id="dosing", section_body="x", expected_revision=3
        )


@respx.mock
def test_person_review_refuses_an_agent_as_accept_does(client) -> None:
    respx.post(f"{FLAG}/resolve-rows").respond(
        403,
        json={
            "detail": {"code": "person_review_required", "message": "Needs a person."},
            "error": {"code": "person_review_required", "message": "Needs a person."},
        },
    )
    with pytest.raises(PersonReviewRequiredError):
        client.knowledge.proposals.resolve_rows("f1", option_id="B", expected_revision=3)


@respx.mock
def test_resolve_on_every_card_sends_the_outcome(client) -> None:
    answer = {"status": "resolved", "proposal_id": "f1", "updated": [], "skipped": []}
    route = respx.post(f"{FLAG}/resolve-cards").respond(200, json=answer)
    assert client.knowledge.proposals.resolve_on_every_card("f1", "keep_disputed") == answer
    assert _body(route) == {"outcome": "keep_disputed"}
    with pytest.raises(ValueError):
        client.knowledge.proposals.resolve_on_every_card("f1", "keep_both")


@respx.mock
async def test_async_resolve_routes_a_row_option(async_client) -> None:
    routes = _serve()
    result = await async_client.knowledge.proposals.resolve(
        "f1", option_id="B", expected_revision=3
    )
    assert result == RESOLVED
    assert _body(routes["rows"]) == {"option_id": "B", "expected_revision": 3}


def test_find_resolution_option_takes_the_letter_or_the_number() -> None:
    options = [SECTION_OPTION, ROW_OPTION]
    assert find_resolution_option(options, "b") is ROW_OPTION
    assert find_resolution_option(options, "1") is SECTION_OPTION
    assert find_resolution_option(options, "3") is None
    assert find_resolution_option(None, "A") is None
