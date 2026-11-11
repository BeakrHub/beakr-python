"""The read trail: what a session read, and what it read before each write."""

from __future__ import annotations

import json

import httpx
import respx

from .conftest import BASE

PHARMA_A = {"id": "pa", "name": "Pharma A"}


def _read(read_id: str, **extra) -> dict:
    return {
        "id": read_id,
        "session_id": "s1",
        "at": "2026-11-11T03:00:00Z",
        "op": "search",
        "projects": [PHARMA_A],
        "items": [
            {"kind": "page", "id": "pg1", "title": "Assay", "project_id": "pa", "service": None}
        ],
        "summary": "assay results",
        "request_id": "r1",
        "client": "sdk-python",
        "agent": None,
        "redacted": False,
        **extra,
    }


def _write_trail() -> dict:
    return {
        "write": {
            "kind": "proposal",
            "id": "pr1",
            "project": PHARMA_A,
            "at": "2026-11-11T03:01:00Z",
        },
        "session_id": "s1",
        "recorded": True,
        "cross_project": False,
        "groups": [
            {"project": PHARMA_A, "cross_project": False, "external": False, "reads": [_read("r1")]}
        ],
        "reads_total": 1,
        "truncated": False,
    }


def test_trail_without_an_id_reads_the_current_sessions(client):
    page = {"data": [_read("r2"), _read("r1")], "next_cursor": None, "has_more": False}
    with respx.mock(base_url=BASE) as router:
        current = router.get("/v1/sessions/current").mock(
            return_value=httpx.Response(200, json={"id": "s1", "status": "active"})
        )
        trail = router.get("/v1/sessions/s1/trail").mock(
            return_value=httpx.Response(200, json=page)
        )
        assert client.sessions.trail(limit=50) == page
    assert current.call_count == 1
    assert dict(trail.calls[0].request.url.params) == {"limit": "50"}


def test_iter_trail_follows_the_cursor(client):
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/sessions/s1/trail").mock(
            side_effect=[
                httpx.Response(
                    200, json={"data": [_read("r3")], "next_cursor": "c2", "has_more": True}
                ),
                httpx.Response(
                    200, json={"data": [_read("r2")], "next_cursor": None, "has_more": False}
                ),
            ]
        )
        reads = list(client.sessions.iter_trail("s1"))
    assert [r["id"] for r in reads] == ["r3", "r2"]


def test_a_started_session_reads_its_own_trail(client):
    with respx.mock(base_url=BASE) as router:
        router.post("/v1/sessions").mock(
            return_value=httpx.Response(201, json={"id": "s1", "status": "active"})
        )
        trail = router.get("/v1/sessions/s1/trail").mock(
            return_value=httpx.Response(
                200, json={"data": [], "next_cursor": None, "has_more": False}
            )
        )
        router.post("/v1/sessions/s1/end").mock(
            return_value=httpx.Response(200, json={"id": "s1", "status": "ended"})
        )
        with client.sessions.start() as s:
            s.trail()
            fresh = s.agent_session_id
    assert trail.calls[0].request.headers["x-beakr-session"] == fresh


def test_a_writes_trail_by_proposal_event_or_revision(client):
    with respx.mock(base_url=BASE) as router:
        proposal = router.get("/v1/knowledge/wiki/proposals/pr1/trail").mock(
            return_value=httpx.Response(200, json=_write_trail())
        )
        event = router.get("/v1/knowledge/wiki/ingestion-events/ev1/trail").mock(
            return_value=httpx.Response(200, json=_write_trail())
        )
        revision = router.get("/v1/knowledge/wiki/pages/pg1/revisions/3/trail").mock(
            return_value=httpx.Response(200, json=_write_trail())
        )
        result = client.knowledge.proposals.trail("pr1")
        client.knowledge.ingestion_event_trail("ev1")
        client.knowledge.pages.revision_trail("pg1", 3)
    assert result["groups"][0]["reads"][0]["op"] == "search"
    assert proposal.call_count == event.call_count == revision.call_count == 1


def test_read_trail_retention_is_set_on_the_active_organization(client):
    settings = {"read_trail_retention_days": 30, "available": True, "can_change": True}
    with respx.mock(base_url=BASE) as router:
        router.get("/v1/me/organization").mock(
            return_value=httpx.Response(200, json={"id": "org1"})
        )
        put = router.put("/v1/organizations/org1/read-trail").mock(
            return_value=httpx.Response(200, json=settings)
        )
        assert client.project_isolation.set_read_trail_retention(30) == settings
    assert json.loads(put.calls[0].request.content) == {"read_trail_retention_days": 30}
