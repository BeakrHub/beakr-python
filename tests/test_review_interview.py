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
    "skipped": [{"proposal_id": "f2", "reason": "not_open"}],
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
        ({"include": ["ambiguity", "likely_error"]}, {"include": ["ambiguity", "likely_error"]}),
        (
            {"proposal_ids": ["f1", "f2"], "max_questions": 5, "participant": "Dana"},
            {"proposal_ids": ["f1", "f2"], "max_questions": 5, "participant": "Dana"},
        ),
        (
            {"title": "Lot 42", "goals": "Settle the dates"},
            {"title": "Lot 42", "goals": "Settle the dates"},
        ),
        ({"proposal_ids": [], "include": []}, {}),
    ],
    ids=["defaults", "include", "named-flags", "title-goals", "empty-lists"],
)
@respx.mock
def test_draft_from_reviews_sends_only_what_was_given(client, kwargs, body) -> None:
    route = respx.post(FROM_REVIEWS).respond(201, json=DRAFTED)
    assert client.interviews.draft_from_reviews("p1", **kwargs) == DRAFTED
    assert json.loads(route.calls.last.request.content) == body
    # A draft is a create: it carries an idempotency key like every write.
    assert route.calls.last.request.headers.get("Idempotency-Key")


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
    answer = await async_client.interviews.draft_from_reviews("p1", include=["all"])
    assert answer == DRAFTED
    assert json.loads(route.calls.last.request.content) == {"include": ["all"]}
