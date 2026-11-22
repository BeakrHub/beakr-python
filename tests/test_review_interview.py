"""Drafting an interview from a project's open review flags, on both clients.

``interviews.draft_from_reviews`` (``POST
/v1/projects/{project_id}/interviews/from-reviews``). Shapes are the engine's
``InterviewFromReviewsBody`` and ``InterviewFromReviewsOut``. The route drafts
and saves an interview; it never sends one.
"""

from __future__ import annotations

import json

import pytest
import respx

from .conftest import BASE

FROM_REVIEWS = f"{BASE}/v1/projects/p1/interviews/from-reviews"
DRAFTED = {
    "interview": {"id": "s1", "title": "Settle the Lot 42 questions", "links": []},
    "questions": [
        {
            "number": 1,
            "question": "Which release date of Lot 42 is right, 3 May or 5 May?",
            "proposal_id": "f1",
            "kind": "contradiction",
            "page_id": "pg1",
            "page_title": "Lot 42",
            "section_id": "release",
            "reason": "Two records give different release dates.",
            "asked_revision": 4,
            "written_by": "model",
            "linked": True,
        }
    ],
    "skipped": [
        {"proposal_id": "f2", "reason": "not_open"},
        {"proposal_id": "f3", "reason": "over_question_limit"},
    ],
    "questions_written_by": "model",
    "edit_url": "https://app.example/interviews/send?edit=s1",
    "empty_reason": None,
    "message": None,
}
NOTHING = {
    "interview": None,
    "questions": [],
    "skipped": [],
    "questions_written_by": None,
    "edit_url": None,
    "empty_reason": "no_open_flags",
    "message": "This project has no open review flags.",
}


@pytest.mark.parametrize(
    ("kwargs", "body"),
    [
        ({}, {}),
        (
            {"kinds": ["contradiction", "ambiguity"], "max_questions": 5},
            {"kinds": ["contradiction", "ambiguity"], "max_questions": 5},
        ),
        ({"proposal_ids": ["f1", "f2"]}, {"proposal_ids": ["f1", "f2"]}),
        (
            {"participant": {"name": "Dana", "email": None}, "link_expires_in_days": 7},
            {"participant": {"name": "Dana"}, "link_expires_in_days": 7},
        ),
        ({"participant": {"user_id": "u1"}}, {"participant": {"user_id": "u1"}}),
        (
            {"title": "Lot 42", "goals": "Settle the dates"},
            {"title": "Lot 42", "goals": "Settle the dates"},
        ),
        ({"proposal_ids": [], "kinds": [], "participant": {}}, {}),
    ],
    ids=["defaults", "kinds", "named-flags", "participant", "member", "title-goals", "empties"],
)
@respx.mock
def test_draft_from_reviews_sends_only_what_was_given(client, kwargs, body) -> None:
    route = respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    assert client.interviews.draft_from_reviews("p1", **kwargs) == DRAFTED
    assert json.loads(route.calls.last.request.content) == body
    # A draft is a create: it carries an idempotency key like every write.
    assert route.calls.last.request.headers.get("Idempotency-Key")


@respx.mock
def test_a_participant_gets_their_link_once(client) -> None:
    with_link = {
        **DRAFTED,
        "link": {
            "link_id": "l1",
            "label": "Dana",
            "expires_at": "2026-12-06T00:00:00Z",
            "result_url": "/v1/interview/sendables/links/l1/result",
        },
        "link_url": "https://app.example/i/tok",
        "secret_redacted": False,
    }
    respx.post(FROM_REVIEWS).respond(201, json=with_link)
    answer = client.interviews.draft_from_reviews("p1", participant={"name": "Dana"})
    assert answer["link_url"] == "https://app.example/i/tok"
    assert answer["link"]["link_id"] == "l1"


@respx.mock
def test_nothing_to_ask_is_a_200_with_no_interview(client) -> None:
    respx.post(FROM_REVIEWS).respond(200, json=NOTHING)
    answer = client.interviews.draft_from_reviews("p1")
    assert answer["interview"] is None
    assert answer["empty_reason"] == "no_open_flags"


@respx.mock
def test_project_id_is_one_encoded_path_segment(client) -> None:
    route = respx.post(f"{BASE}/v1/projects/p%2F1/interviews/from-reviews").respond(
        201, json=DRAFTED
    )
    client.interviews.draft_from_reviews("p/1")
    assert route.called


@respx.mock
async def test_async_draft_from_reviews(async_client) -> None:
    route = respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    answer = await async_client.interviews.draft_from_reviews("p1", kinds=["other"])
    assert answer == DRAFTED
    assert json.loads(route.calls.last.request.content) == {"kinds": ["other"]}
