"""Project boundaries: session modes, the options and status routes, a project's
boundary, the organization's settings, and the boundary refusals as typed errors.

A session works one of three ways (``mode``): automatic (nothing declared),
picked (``read``/``write``) or read everything. ``sessions.options`` checks picked
lists before starting, and ``sessions.status`` says where a session can still
write. Each refusal code has its own error type.
"""

from __future__ import annotations

import json
import warnings

import httpx
import pytest
import respx

from beakr_sdk import (
    BoundaryBelowMinimumError,
    ProjectContentLockedError,
    SessionAlreadyStartedError,
    SessionSelectionInvalidError,
    SettingRetiredError,
)

from .conftest import BASE

A = "pa"
CHEM = "pc"


def _refusal(status: int, code: str, message: str, **extras) -> httpx.Response:
    # As the engine sends it: the extras beside code in ``detail`` and in ``error.details``.
    detail = {"code": code, "message": message, "fix": "Do this.", **extras}
    error = {"code": code, "message": message, "fix": "Do this.", "details": extras}
    return httpx.Response(status, json={"detail": detail, "error": error})


def _session(**extra) -> dict:
    return {
        "id": "s1",
        "status": "active",
        "mode": "picked",
        "summary": "Picked session: reads Partner A; writes Partner A.",
        "notes": [],
        **extra,
    }


def _body(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


# -- session modes ---------------------------------------------------------------


def test_create_read_everything_sends_the_mode_and_no_lists(client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/sessions").mock(
            return_value=httpx.Response(
                201,
                json={
                    **_session(mode="read_everything"),
                    "token": {"token": "bk_st_x", "expires_at": "2099-01-01T00:00:00Z"},
                },
            )
        )
        scoped = client.sessions.create(read_everything=True)
    body = _body(created)
    assert body["read_everything"] is True
    assert "read_project_ids" not in body and "write_project_ids" not in body
    assert scoped.session["mode"] == "read_everything"


def test_start_declares_a_picked_session(client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json=_session())
        )
        router.post("/v1/sessions/s1/end").mock(return_value=httpx.Response(200, json=_session()))
        with client.sessions.start(write=[A]) as s:
            assert s.session["summary"].startswith("Picked session")
    body = _body(created)
    assert body["read_project_ids"] == [A] and body["write_project_ids"] == [A]
    assert body["agent_session"].startswith("sdk-python-")


def test_update_read_everything(client):
    with respx.mock(base_url=BASE) as router:
        patched = router.patch("/v1/sessions/s1").mock(
            return_value=httpx.Response(200, json=_session(mode="read_everything"))
        )
        client.sessions.update("s1", read_everything=True)
    assert _body(patched) == {"read_everything": True}


def test_a_choice_that_breaks_a_boundary_is_typed_with_every_reason(client):
    reasons = [
        {
            "code": "locked_read_limits_writes",
            "message": "Reading Partner A locks this session's writes to Partner A.",
            "project_ids": [A, CHEM],
        },
        {
            "code": "blocked_write_limits_reads",
            "message": "Chemistry takes no content from other projects.",
            "project_ids": [CHEM],
        },
    ]
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(
            return_value=_refusal(
                422, "session_selection_invalid", reasons[0]["message"], reasons=reasons
            )
        )
        with pytest.raises(SessionSelectionInvalidError) as raised:
            client.sessions.create(read=[A], write=[CHEM])
    assert [r["code"] for r in raised.value.reasons] == [
        "locked_read_limits_writes",
        "blocked_write_limits_reads",
    ]
    assert raised.value.reason_messages[1] == "Chemistry takes no content from other projects."


def test_declaring_a_session_that_already_worked_is_typed(client):
    with respx.mock(base_url=BASE) as router:
        router.patch("/v1/sessions/s1").mock(
            return_value=_refusal(
                409,
                "session_already_started",
                "This session has already worked in Partner A, so its projects can't be chosen "
                "now.",
            )
        )
        with pytest.raises(SessionAlreadyStartedError):
            client.sessions.update("s1", read_everything=True)


# -- options and status ------------------------------------------------------------


def test_options_sends_the_lists_so_far(client):
    answer = {"valid": True, "reasons": [], "notes": [], "projects": [], "read_everything": {}}
    with respx.mock(base_url=BASE) as router:
        route = router.post("/v1/sessions/options").mock(
            return_value=httpx.Response(200, json=answer)
        )
        assert client.sessions.options(read=[A]) == answer
        client.sessions.options()
    assert json.loads(route.calls[0].request.content) == {
        "read_project_ids": [A],
        "write_project_ids": [],
    }
    assert _body(route) == {"read_project_ids": [], "write_project_ids": []}


def test_status_of_the_current_session_or_one_by_id(client):
    status = {"session_id": "s1", "mode": "automatic", "summary": "Automatic session."}
    with respx.mock(base_url=BASE) as router:
        current = router.get("/v1/sessions/current/status").mock(
            return_value=httpx.Response(200, json=status)
        )
        by_id = router.get("/v1/sessions/s1/status").mock(
            return_value=httpx.Response(200, json=status)
        )
        assert client.sessions.status() == status
        assert client.sessions.status("s1") == status
    assert current.call_count == 1 and by_id.call_count == 1


async def test_async_status(async_client):
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/sessions/current/status").mock(
            return_value=httpx.Response(200, json={"session_id": "s1"})
        )
        assert (await async_client.sessions.status())["session_id"] == "s1"


# -- a project's boundary ----------------------------------------------------------


def test_set_boundary_sends_a_preset_or_the_values(client):
    project = {"id": A, "name": "Partner A", "boundary": {"preset": "siloed"}}
    with respx.mock(base_url=BASE) as router:
        patched = router.patch(f"/v1/projects/{A}").mock(
            return_value=httpx.Response(200, json=project)
        )
        assert client.projects.set_boundary(A, preset="siloed") == project
        assert _body(patched) == {"boundary_preset": "siloed"}
        client.projects.set_boundary(A, content_in="blocked")
        assert _body(patched) == {"boundary": {"content_in": "blocked"}}
    with pytest.raises(ValueError):
        client.projects.set_boundary(A, preset="siloed", content_out="locked")
    with pytest.raises(ValueError):
        client.projects.set_boundary(A)


def test_create_takes_a_boundary(client):
    with respx.mock(base_url=BASE) as router:
        created = router.post("/v1/projects").mock(
            return_value=httpx.Response(201, json={"id": A, "name": "Partner A"})
        )
        client.projects.create("Partner A", boundary_preset="siloed")
    assert _body(created) == {"name": "Partner A", "boundary_preset": "siloed"}


def test_a_boundary_below_the_minimum_is_typed(client):
    with respx.mock(base_url=BASE) as router:
        router.patch(f"/v1/projects/{A}").mock(
            return_value=_refusal(
                422,
                "boundary_below_minimum",
                "Your organization's minimum boundary is {content_out: locked}.",
                minimum={"content_out": "locked"},
            )
        )
        with pytest.raises(BoundaryBelowMinimumError) as raised:
            client.projects.set_boundary(A, content_out="allowed")
    assert raised.value.minimum == {"content_out": "locked"}


# -- the organization's settings ----------------------------------------------------


def test_update_sends_the_boundaries_and_clears_the_minimum_only_when_asked(client):
    with respx.mock(base_url=BASE) as router:
        put = router.put("/v1/organizations/org1/project-isolation").mock(
            return_value=httpx.Response(200, json={})
        )
        client.project_isolation.update(
            default_boundary={"content_out": "locked", "content_in": "owner_approves"},
            org_id="org1",
        )
        assert _body(put) == {
            "default_boundary": {"content_out": "locked", "content_in": "owner_approves"}
        }
        client.project_isolation.update(minimum_boundary=None, org_id="org1")
        assert _body(put) == {"minimum_boundary": None}
        client.project_isolation.update(minimum_boundary={"content_out": "locked"}, org_id="org1")
        assert _body(put) == {"minimum_boundary": {"content_out": "locked"}}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cross_project_guard": "hold"},
        {"auto_lock_counts_search_results": False},
        {"session_mode": "auto_lock"},
        {"session_mode": "picker"},
    ],
)
def test_a_retired_setting_warns_and_the_server_refuses_it(client, kwargs):
    with respx.mock(base_url=BASE) as router:
        router.put("/v1/organizations/org1/project-isolation").mock(
            return_value=_refusal(
                422,
                "setting_retired",
                "cross_project_guard was replaced by project boundaries: set each project's "
                "content_in instead.",
            )
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(SettingRetiredError):
                client.project_isolation.update(org_id="org1", **kwargs)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# -- the refusals -------------------------------------------------------------------


def test_a_write_that_carries_a_locked_projects_content_is_typed(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/knowledge/wiki/proposals").mock(
            return_value=_refusal(
                403,
                "project_content_locked",
                "This write brings in content from Partner A (cited), which keeps its content "
                "inside it, so it can't go into Chemistry. Nothing was written.",
                session_id="s1",
                project={"id": CHEM, "name": "Chemistry"},
                other_projects=[{"id": A, "name": "Partner A", "via": ["cited"]}],
            )
        )
        with pytest.raises(ProjectContentLockedError) as raised:
            client.request("POST", "/v1/knowledge/wiki/proposals", json={})
    assert raised.value.session_id == "s1"
    assert raised.value.project == {"id": CHEM, "name": "Chemistry"}
    assert raised.value.other_projects[0]["name"] == "Partner A"
