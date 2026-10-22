"""Agent review (engine #1717 and follow-ups): the policy, held files, typed refusals.

``knowledge.agent_review`` reads and merges the organization's and a project's
policy; ``files.approve_knowledge`` compiles a file waiting for a person; a key
the policy holds back raises ``PersonReviewRequiredError`` and a change only a
person may make raises ``SessionRequiredError``, both ``PermissionDeniedError``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_sdk import (
    PermissionDeniedError,
    PersonReviewRequiredError,
    SessionRequiredError,
)
from beakr_sdk.errors import error_from_response

from .conftest import BASE

URL = f"{BASE}/v1/knowledge/agent-review"
POLICY = {
    "agent_proposals_need_a_person": True,
    "agent_uploads_need_a_person": False,
    "capture_auto_approve_allowed": False,
}
SETTINGS = {"available": True, "org": POLICY, "effective": POLICY, "project_override": None}


def _refusal(code: str) -> dict:
    return {"detail": "m", "error": {"code": code, "message": "A person must accept this."}}


@respx.mock
def test_get_reads_the_policy_for_a_project(client) -> None:
    route = respx.get(URL).respond(200, json=SETTINGS)
    assert client.knowledge.agent_review.get(project_id="p1")["effective"] == POLICY
    assert route.calls.last.request.url.params["project_id"] == "p1"


@respx.mock
def test_set_sends_only_the_fields_given_and_none_unsets(client) -> None:
    route = respx.put(URL).respond(200, json=SETTINGS)
    client.knowledge.agent_review.set(
        agent_proposals_need_a_person=True, capture_auto_approve_allowed=None
    )
    assert json.loads(route.calls.last.request.content) == {
        "agent_proposals_need_a_person": True,
        "capture_auto_approve_allowed": None,
    }


@respx.mock
def test_a_project_sets_its_own_fields_or_follows_the_organization(client) -> None:
    route = respx.put(f"{URL}/projects/p1").respond(200, json=SETTINGS)
    client.knowledge.agent_review.set_project("p1", agent_uploads_need_a_person=True)
    assert json.loads(route.calls.last.request.content) == {
        "policy": {"agent_uploads_need_a_person": True}
    }
    client.knowledge.agent_review.follow_org("p1")
    assert json.loads(route.calls.last.request.content) == {"policy": None}


@respx.mock
async def test_the_async_client_reads_and_sets_it(async_client) -> None:
    respx.get(URL).respond(200, json=SETTINGS)
    route = respx.put(URL).respond(200, json=SETTINGS)
    assert (await async_client.knowledge.agent_review.get())["available"] is True
    await async_client.knowledge.agent_review.set(agent_uploads_need_a_person=False)
    assert json.loads(route.calls.last.request.content) == {"agent_uploads_need_a_person": False}


@respx.mock
def test_approve_knowledge_compiles_a_held_file(client) -> None:
    route = respx.post(f"{BASE}/v1/artifacts/a1/knowledge/approve").respond(
        200, json={"queue_result": "queued"}
    )
    assert client.files.approve_knowledge("a1") == {"queue_result": "queued"}
    assert route.called


@respx.mock
def test_a_held_back_accept_raises_person_review_required(client) -> None:
    respx.post(f"{BASE}/v1/knowledge/wiki/proposals/wp1/accept").respond(
        403, json=_refusal("person_review_required")
    )
    with pytest.raises(PersonReviewRequiredError) as caught:
        client.knowledge.proposals.accept("wp1")
    assert isinstance(caught.value, PermissionDeniedError)
    assert caught.value.code == "person_review_required"
    assert caught.value.message == "A person must accept this."


@respx.mock
async def test_the_async_client_raises_person_review_required(async_client) -> None:
    respx.post(f"{BASE}/v1/artifacts/a1/knowledge/approve").respond(
        403, json=_refusal("person_review_required")
    )
    with pytest.raises(PersonReviewRequiredError):
        await async_client.files.approve_knowledge("a1")


@respx.mock
def test_a_change_only_a_person_may_make_raises_session_required(client) -> None:
    respx.put(URL).respond(403, json=_refusal("session_required"))
    with pytest.raises(SessionRequiredError) as caught:
        client.knowledge.agent_review.set(agent_proposals_need_a_person=False)
    assert isinstance(caught.value, PermissionDeniedError)
    assert caught.value.code == "session_required"


def test_other_403s_stay_permission_denied() -> None:
    error = error_from_response(
        httpx.Response(
            403,
            json={"error": {"code": "insufficient_scope", "details": {"required_scope": "x"}}},
            request=httpx.Request("GET", f"{BASE}/v1/projects"),
        )
    )
    assert type(error) is PermissionDeniedError
    assert error.required_scope == "x"
