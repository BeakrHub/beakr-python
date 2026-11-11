"""Project isolation: one server-side session per client, its helpers and refusals.

Every client sends an ``X-Beakr-Session`` id (tests/test_transport.py pins
where it comes from); the server keys a person's session on it, so auto-lock
and the cross-project guard apply to an SDK script as to an agent.
``sessions.start`` opens a fresh one for a block of work.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_sdk import (
    AsyncBeakr,
    ConflictError,
    CrossProjectOwnerRequiredError,
    CrossProjectWriteRefusedError,
    PermissionDeniedError,
    SessionEndedError,
    SessionLockedToProjectError,
    SessionReadOnlyError,
    SessionRequiredError,
    cross_project_hold,
)

from .conftest import BASE

PHARMA_A = {"id": "pa", "name": "Pharma A"}
PHARMA_B = {"id": "pb", "name": "Pharma B"}


def _session(**extra) -> dict:
    return {
        "id": "s1",
        "status": "active",
        "mode": "auto_lock",
        "agent_session": "conv-1",
        "lock_state": "open",
        "write_project": None,
        "touched_projects": [],
        "read_projects": [],
        "write_projects": [],
        **extra,
    }


def _refusal(status: int, code: str, message: str, **extras) -> httpx.Response:
    # As the engine sends it: the extras beside code in ``detail`` and in
    # ``error.details``.
    detail = {"code": code, "message": message, "fix": "Start a new session.", **extras}
    error = {"code": code, "message": message, "fix": "Start a new session.", "details": extras}
    return httpx.Response(status, json={"detail": detail, "error": error})


# -- sessions.start ----------------------------------------------------------


def test_start_opens_a_fresh_session_and_ends_it(client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_session())
        )
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={}))
        ended = router.post("/v1/sessions/s1/end").mock(
            return_value=httpx.Response(200, json=_session(status="ended"))
        )
        with client.sessions.start(label="nightly import") as s:
            s.client.me.get()
            client.me.get()
            assert ended.call_count == 0
    body = json.loads(created.calls[0].request.content)
    fresh = body["agent_session"]
    assert body == {
        "agent_session": fresh,
        "label": "nightly import",
        "client_name": "sdk-python",
    }
    assert fresh.startswith("sdk-python-") and fresh != client.session
    assert s.agent_session_id == fresh and s.id == "s1"
    # The session's client sends the fresh id; the client that started it keeps its own.
    sent = [call.request.headers["x-beakr-session"] for call in me.calls]
    assert sent == [fresh, client.session]
    # Same caller: the session's client sends the starting client's credential.
    assert me.calls[0].request.headers["authorization"] == "Bearer bk_live_test"
    assert ended.call_count == 1
    assert ended.calls[0].request.headers["x-beakr-session"] == fresh


def test_start_ends_the_session_when_the_block_raises(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(return_value=httpx.Response(201, json=_session()))
        ended = router.post("/v1/sessions/s1/end").mock(
            return_value=httpx.Response(200, json=_session(status="ended"))
        )
        with pytest.raises(RuntimeError), client.sessions.start():
            raise RuntimeError("boom")
    assert ended.call_count == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [(422, "feature_not_enabled"), (404, "not_found")],
    ids=["no sessions per conversation", "no sessions"],
)
def test_start_on_a_server_without_sessions_per_conversation_only_labels(client, status, code):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(return_value=_refusal(status, code, "Not enabled."))
        me = router.get("/v1/me").mock(return_value=httpx.Response(200, json={}))
        with client.sessions.start() as s:
            s.client.me.get()
        # Nothing to end: only the create and the read went out.
        assert len(router.calls) == 2
    assert s.session is None and s.id is None
    assert me.calls[0].request.headers["x-beakr-session"] == s.agent_session_id


def test_start_raises_other_refusals(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(
            return_value=_refusal(422, "session_mode_auto_lock", "Auto-lock picks projects.")
        )
        with pytest.raises(Exception) as caught, client.sessions.start():
            pass
    assert getattr(caught.value, "code", None) == "session_mode_auto_lock"


async def test_async_start_opens_and_ends_a_session(async_client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_session())
        )
        current = router.get("/v1/sessions/current").mock(
            return_value=httpx.Response(200, json=_session(lock_state="locked"))
        )
        ended = router.post("/v1/sessions/s1/end").mock(
            return_value=httpx.Response(200, json=_session(status="ended"))
        )
        async with async_client.sessions.start(label="job") as s:
            assert isinstance(s.client, AsyncBeakr)
            assert (await s.current())["lock_state"] == "locked"
    fresh = json.loads(created.calls[0].request.content)["agent_session"]
    assert current.calls[0].request.headers["x-beakr-session"] == fresh
    assert ended.call_count == 1


# -- current and end ----------------------------------------------------------


def test_end_without_an_id_ends_the_current_session(client):
    with respx.mock(base_url=BASE) as router:
        current = router.get("/v1/sessions/current").mock(
            return_value=httpx.Response(200, json=_session(id="s9"))
        )
        ended = router.post("/v1/sessions/s9/end").mock(
            return_value=httpx.Response(200, json=_session(id="s9", status="ended"))
        )
        result = client.sessions.end()
    assert result["status"] == "ended"
    assert current.calls[0].request.headers["x-beakr-session"] == client.session
    assert ended.call_count == 1


def test_an_ended_session_id_is_refused_with_its_own_error(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/knowledge/wiki/write").mock(
            return_value=_refusal(409, "session_ended", "This session has ended.")
        )
        with pytest.raises(SessionEndedError) as caught:
            client.knowledge.write("add_fact", rationale="r")
    assert isinstance(caught.value, ConflictError)


# -- refusals -----------------------------------------------------------------


def test_a_read_only_session_names_the_projects_it_read(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/knowledge/wiki/write").mock(
            return_value=_refusal(
                403,
                "session_read_only",
                "This session read Pharma A and Pharma B, so it is read-only.",
                session_id="s1",
                touched_projects=[PHARMA_A, PHARMA_B],
            )
        )
        with pytest.raises(SessionReadOnlyError) as caught:
            client.knowledge.write("add_fact", rationale="r")
    error = caught.value
    assert isinstance(error, PermissionDeniedError)
    assert error.session_id == "s1"
    assert error.touched_projects == [PHARMA_A, PHARMA_B]
    assert error.project_names == ["Pharma A", "Pharma B"]
    assert "Pharma A and Pharma B" in str(error)


def test_a_locked_session_names_its_project_and_the_refused_one(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/folders").mock(
            return_value=_refusal(
                403,
                "session_locked_to_project",
                "This session reads and writes Pharma A, so it cannot write Pharma B.",
                session_id="s1",
                write_project=PHARMA_A,
                refused_projects=[PHARMA_B],
            )
        )
        with pytest.raises(SessionLockedToProjectError) as caught:
            client.folders.create("Notes", project_id="pb")
    assert caught.value.write_project == PHARMA_A
    assert caught.value.refused_projects == [PHARMA_B]


def test_a_write_outside_a_session_is_session_required(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/knowledge/wiki/write").mock(
            return_value=_refusal(
                403, "session_required", "Your organization requires agents to write in a session."
            )
        )
        with pytest.raises(SessionRequiredError):
            client.knowledge.write("add_fact", rationale="r")


def test_the_cross_project_guard_refusals_are_typed(client):
    via = {**PHARMA_A, "via": ["cited", "read_in_session"]}
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/knowledge/wiki/write").mock(
            return_value=_refusal(
                403,
                "cross_project_write_refused",
                "Pharma B does not accept content from other projects.",
                project=PHARMA_B,
                other_projects=[via],
            )
        )
        router.post("/v1/knowledge/wiki/proposals/p1/accept").mock(
            return_value=_refusal(
                403, "cross_project_owner_required", "Only an owner or admin of Pharma B."
            )
        )
        with pytest.raises(CrossProjectWriteRefusedError) as refused:
            client.knowledge.write("add_fact", rationale="r")
        with pytest.raises(CrossProjectOwnerRequiredError):
            client.knowledge.proposals.accept("p1")
    assert refused.value.project == PHARMA_B
    assert refused.value.other_projects == [via]
    assert refused.value.project_names == ["Pharma A"]


# -- held for approval --------------------------------------------------------


def test_a_held_write_is_a_normal_result_that_says_why(client):
    hold = {
        "reason": "Content from Pharma A (cited).",
        "projects": [{**PHARMA_A, "via": ["cited"]}],
        "approver": "owner_or_admin",
        "project": PHARMA_B,
    }
    staged = {
        "content": [{"type": "text", "text": "Staged."}],
        "outcome": {"status": "ok", "approval_state": "pending"},
        "proposal_id": "p1",
        "proposal": {"id": "p1", "cross_project_hold": hold},
    }
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/knowledge/wiki/write").mock(return_value=httpx.Response(200, json=staged))
        router.get("/v1/knowledge/wiki/proposals/p1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "p1",
                    "cross_project_hold": hold,
                    "needs_project_admin_to_accept": True,
                },
            )
        )
        result = client.knowledge.write("add_fact", rationale="r")
        proposal = client.knowledge.proposals.show("p1")
    assert cross_project_hold(result) == hold
    assert cross_project_hold(proposal) == hold
    assert cross_project_hold({"id": "p2", "cross_project_hold": None}) is None
    assert cross_project_hold(None) is None


# -- settings -------------------------------------------------------------------


def test_the_settings_and_a_projects_guard_send_only_what_changes(client):
    settings = {
        "session_mode": "auto_lock",
        "auto_lock_counts_search_results": True,
        "agent_writes_require_session": True,
        "cross_project_guard": "hold",
        "available": {
            "server_agent_sessions": True,
            "session_auto_lock": True,
            "agent_writes_require_session": True,
            "cross_project_guard": True,
        },
        "can_change": True,
    }
    guard = {
        "project_id": "pb",
        "project_name": "Pharma B",
        "override": None,
        "org_default": "hold",
        "effective": "hold",
        "can_change": True,
        "available": True,
    }
    with respx.mock(base_url=BASE) as router:
        active = router.get("/v1/me/organization").mock(
            return_value=httpx.Response(200, json={"id": "org1"})
        )
        put = router.put("/v1/organizations/org1/project-isolation").mock(
            return_value=httpx.Response(200, json=settings)
        )
        set_guard = router.put("/v1/projects/pb/cross-project-guard").mock(
            return_value=httpx.Response(200, json=guard)
        )
        assert client.project_isolation.update(session_mode="auto_lock") == settings
        client.projects.set_cross_project_guard("pb", None)
        client.projects.set_cross_project_guard("pb", "refuse")
    # The active organization's, looked up first.
    assert active.call_count == 1
    assert json.loads(put.calls[0].request.content) == {"session_mode": "auto_lock"}
    # None is sent: it clears the project's own guard, so it follows the org.
    assert [json.loads(c.request.content) for c in set_guard.calls] == [
        {"override": None},
        {"override": "refuse"},
    ]
