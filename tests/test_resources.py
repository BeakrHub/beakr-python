"""Every resource method sends the method, path, query and body the engine expects.

Shapes for existing routes were taken from the engine's route definitions, not
the contract summary: skills live under /v1/me/skills, an org role is a query
parameter, profile claims are confirmed with PATCH {"confirm": true}.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx

from beakr_sdk import KnowledgeCommandError

from .conftest import BASE

# (label, call, method, path, expected query, expected json body or None)

# Whole-list methods page through the list at the largest page size.
PAGE = {"limit": "200"}

CASES: list[tuple[str, Any, str, str, dict[str, str], Any]] = [
    ("me", lambda c: c.me.get(), "GET", "/v1/me", {}, None),
    ("key scopes", lambda c: c.keys.scopes(), "GET", "/v1/me/api-keys/scopes", {}, None),
    (
        "key create",
        lambda c: c.keys.create(
            "ci", scopes=["kb:read"], project_ids=["p1"], expires_in_days=30, client_name="ci-bot"
        ),
        "POST",
        "/v1/me/api-keys",
        {},
        {
            "name": "ci",
            "scopes": ["kb:read"],
            "project_ids": ["p1"],
            "expires_in_days": 30,
            "client_name": "ci-bot",
        },
    ),
    ("key revoke", lambda c: c.keys.revoke("k1"), "DELETE", "/v1/me/api-keys/k1", {}, None),
    ("projects", lambda c: c.projects.list(), "GET", "/v1/projects", PAGE, None),
    (
        "project create",
        lambda c: c.projects.create("R", description="d"),
        "POST",
        "/v1/projects",
        {},
        {"name": "R", "description": "d"},
    ),
    (
        "project archive",
        lambda c: c.projects.archive("p1"),
        "PATCH",
        "/v1/projects/p1",
        {},
        {"status": "archived"},
    ),
    (
        "members",
        lambda c: c.members.list(org_id="o1"),
        "GET",
        "/v1/organizations/o1/members",
        PAGE,
        None,
    ),
    (
        "member role",
        lambda c: c.members.set_role("u1", "admin", org_id="o1"),
        "PATCH",
        "/v1/organizations/o1/members/u1",
        {"new_role": "admin"},
        None,
    ),
    (
        "member rm",
        lambda c: c.members.remove("u1", org_id="o1"),
        "DELETE",
        "/v1/organizations/o1/members/u1",
        {},
        None,
    ),
    (
        "files",
        lambda c: c.files.list(project_id="p1", search="q"),
        "GET",
        "/v1/artifacts",
        {"project_id": "p1", "search": "q", "limit": "50"},
        None,
    ),
    ("file content", lambda c: c.files.content("a1"), "GET", "/v1/artifacts/a1/content", {}, None),
    ("file rm", lambda c: c.files.delete("a1"), "DELETE", "/v1/artifacts/a1", {}, None),
    (
        "folders",
        lambda c: c.folders.list("p1", root_only=True),
        "GET",
        "/v1/folders",
        {"project_id": "p1", "root_only": "true", **PAGE},
        None,
    ),
    (
        "folder create",
        lambda c: c.folders.create("Docs", project_id="p1"),
        "POST",
        "/v1/folders",
        {},
        {"name": "Docs", "project_id": "p1"},
    ),
    (
        "rows",
        lambda c: c.knowledge.rows("facts", entity="Stripe", current_only=True, scope="Platform"),
        "POST",
        "/v1/knowledge/wiki/command",
        {},
        {
            "command": "rows",
            "table": "facts",
            "entity": "Stripe",
            "current_only": True,
            "scope": "Platform",
        },
    ),
    (
        "rows with the map-walk arguments",
        lambda c: c.knowledge.rows(
            "connections",
            page_type="matter",
            entity="Stripe",
            to="Acme",
            connected_to="Acme",
            group_by="relation",
            regex=True,
            view="values",
            match="similar",
            scope="Platform",
        ),
        "POST",
        "/v1/knowledge/wiki/command",
        {},
        {
            "command": "rows",
            "table": "connections",
            "page_type": "matter",
            "entity": "Stripe",
            "to": "Acme",
            "connected_to": "Acme",
            "group_by": "relation",
            "regex": True,
            "view": "values",
            "match": "similar",
            "scope": "Platform",
        },
    ),
    (
        "rows as of a date and by field id",
        lambda c: c.knowledge.rows(as_of="2026-01-31", field_id="facts:abc", after_card="c1"),
        "POST",
        "/v1/knowledge/wiki/command",
        {},
        {
            "command": "rows",
            "table": "facts",
            "as_of": "2026-01-31",
            "field_id": "facts:abc",
            "after_card": "c1",
        },
    ),
    (
        "timeline",
        lambda c: c.knowledge.timeline("launch", start_date="2026-01-01"),
        "POST",
        "/v1/knowledge/wiki/command",
        {},
        {"command": "timeline", "timeline_query": "launch", "start_date": "2026-01-01"},
    ),
    (
        "sections",
        lambda c: c.knowledge.search("owner", limit=5),
        "POST",
        "/v1/knowledge/wiki/command",
        {},
        {"command": "sections", "query": "owner", "limit": 5},
    ),
    (
        "add_fact",
        lambda c: c.knowledge.add_fact(
            "Stripe",
            attribute="tier",
            value="Pro",
            evidence="{{doc:1}}",
            when="2026-03",
            rationale="why",
            scope="Platform",
        ),
        "POST",
        "/v1/knowledge/wiki/write",
        {},
        {
            "action": "add_fact",
            "page": "Stripe",
            "attribute": "tier",
            "value": "Pro",
            "evidence": "{{doc:1}}",
            "when": "2026-03",
            "rationale": "why",
            "scope": "Platform",
        },
    ),
    (
        "add_connection",
        lambda c: c.knowledge.add_connection("A", relation="owns", to="B", evidence="{{doc:1}}"),
        "POST",
        "/v1/knowledge/wiki/write",
        {},
        {
            "action": "add_connection",
            "page": "A",
            "relation": "owns",
            "to": "B",
            "evidence": "{{doc:1}}",
        },
    ),
    (
        "add_finding",
        lambda c: c.knowledge.add_finding(
            "A", date="2026-10-01", kind="lesson", text="t", evidence="{{doc:1}}", about=["x"]
        ),
        "POST",
        "/v1/knowledge/wiki/write",
        {},
        {
            "action": "add_finding",
            "page": "A",
            "date": "2026-10-01",
            "kind": "lesson",
            "text": "t",
            "evidence": "{{doc:1}}",
            "about": ["x"],
        },
    ),
    (
        "edit_rows delete",
        lambda c: c.knowledge.edit_rows(
            "A", table="facts", old_rows=[{"attribute": "a", "value": "v"}], rows=[], reason="dup"
        ),
        "POST",
        "/v1/knowledge/wiki/write",
        {},
        {
            "action": "edit_rows",
            "page": "A",
            "table": "facts",
            "old_rows": [{"attribute": "a", "value": "v"}],
            "rows": [],
            "reason": "dup",
        },
    ),
    (
        "proposals",
        lambda c: c.knowledge.proposals.list(project_id="p1"),
        "GET",
        "/v1/knowledge/wiki/proposals",
        {"status": "pending", "project_id": "p1", "limit": "50"},
        None,
    ),
    (
        "query",
        lambda c: c.knowledge.query(
            {
                "from": {"table": "facts", "as": "f"},
                "where": {
                    "and": [
                        {"entity_type": "agreement"},
                        {"field": "governing law", "match": "similar"},
                        {"not": {"value": "Delaware", "op": "contains"}},
                    ]
                },
                "join": [{"table": "connections", "as": "c", "on": "same_card", "with": "f"}],
                "group_by": ["value"],
                "aggregate": [{"fn": "count", "of": "entity"}],
                "order_by": [{"by": "count", "dir": "desc"}],
                "limit": 20,
            },
            scope="Contracts",
        ),
        "POST",
        "/v1/knowledge/wiki/command",
        {},
        {
            "command": "query",
            "spec": {
                "from": {"table": "facts", "as": "f"},
                "where": {
                    "and": [
                        {"entity_type": "agreement"},
                        {"field": "governing law", "match": "similar"},
                        {"not": {"value": "Delaware", "op": "contains"}},
                    ]
                },
                "join": [{"table": "connections", "as": "c", "on": "same_card", "with": "f"}],
                "group_by": ["value"],
                "aggregate": [{"fn": "count", "of": "entity"}],
                "order_by": [{"by": "count", "dir": "desc"}],
                "limit": 20,
            },
            "scope": "Contracts",
        },
    ),
    (
        "accept",
        lambda c: c.knowledge.proposals.accept("x1"),
        "POST",
        "/v1/knowledge/wiki/proposals/x1/accept",
        {},
        None,
    ),
    (
        # The hash of the read that was reviewed, sent unchanged; the helper never
        # fetches the proposal to fill it in (security review, 2026-10-16).
        "accept_reviewed",
        lambda c: c.knowledge.proposals.accept("x1", reviewed_hash="ab" * 32),
        "POST",
        "/v1/knowledge/wiki/proposals/x1/accept",
        {},
        {"reviewed_hash": "ab" * 32},
    ),
    (
        "research",
        lambda c: c.knowledge.research("why?", project_id="p1"),
        "POST",
        "/v1/knowledge/research",
        {},
        {"query": "why?", "project_id": "p1"},
    ),
    (
        "conv import",
        lambda c: c.conversations.import_(
            client="codex", session_id="s1", messages=[{"role": "user", "content": "hi"}]
        ),
        "POST",
        "/v1/conversations/import",
        {},
        {
            "client": "codex",
            "session_id": "s1",
            "messages": [{"role": "user", "content": "hi"}],
            "interactive": False,
        },
    ),
    (
        "conv rm",
        lambda c: c.conversations.delete("cv1"),
        "DELETE",
        "/v1/conversations/cv1",
        {},
        None,
    ),
    (
        "context",
        lambda c: c.context(project_id="p1", format="claude_md", budget_chars=4000),
        "POST",
        "/v1/context",
        {},
        {"project_id": "p1", "format": "claude_md", "budget_chars": 4000},
    ),
    (
        "compound",
        lambda c: c.compound("cv1"),
        "POST",
        "/v1/compound",
        {},
        {"conversation_id": "cv1"},
    ),
    ("run get", lambda c: c.runs.get("r1"), "GET", "/v1/runs/r1", {}, None),
    (
        "threads",
        lambda c: c.threads.list(limit=5),
        "GET",
        "/v1/me/threads",
        {"limit": "5"},
        None,
    ),
]


@pytest.mark.parametrize(
    ("label", "invoke", "method", "path", "query", "body"), CASES, ids=[c[0] for c in CASES]
)
@respx.mock
def test_resource_request_shape(client, label, invoke, method, path, query, body) -> None:
    route = respx.route(method=method, url=f"{BASE}{path}").respond(
        200, json={"content": [], "outcome": {"status": "success"}, "id": "x"}
    )
    invoke(client)
    request = route.calls.last.request
    assert dict(request.url.params) == query
    if body is None:
        assert not request.content
    else:
        assert json.loads(request.content) == body


@respx.mock
def test_members_default_to_the_active_org(client) -> None:
    respx.get(f"{BASE}/v1/me/organization").respond(json={"id": "org-9"})
    route = respx.get(f"{BASE}/v1/organizations/org-9/members").respond(json=[{"user_id": "u"}])
    assert client.members.list() == [{"user_id": "u"}]
    assert route.called


@respx.mock
def test_run_start_sends_a_body_idempotency_key_and_project_scope(client) -> None:
    route = respx.post(f"{BASE}/v1/runs").respond(json={"run_id": "r"})
    client.runs.start("hello", project_id="p1")
    body = json.loads(route.calls.last.request.content)
    assert body["message"] == "hello"
    assert body["home_project_id"] == "p1"
    assert body["scope"] == {"projects": [{"id": "p1"}]}
    assert len(body["idempotency_key"]) == 32


@respx.mock
def test_knowledge_refusal_raises_with_the_doors_guidance(client) -> None:
    respx.post(f"{BASE}/v1/knowledge/wiki/write").respond(
        200,
        json={
            "content": [{"type": "text", "text": "Not a card: use edit_section."}],
            "outcome": {"status": "error", "error_code": "not_a_card"},
        },
    )
    with pytest.raises(KnowledgeCommandError) as caught:
        client.knowledge.add_fact("Readme", attribute="a", value="b", evidence="{{x}}")
    assert caught.value.code == "not_a_card"
    assert "edit_section" in caught.value.message
    assert caught.value.result["outcome"]["status"] == "error"


@respx.mock
def test_knowledge_check_false_returns_the_refusal(client) -> None:
    respx.post(f"{BASE}/v1/knowledge/wiki/command").respond(
        200, json={"content": [], "outcome": {"status": "error"}}
    )
    result = client.knowledge.command("cat", page="x", check=False)
    assert result["outcome"]["status"] == "error"


@respx.mock
def test_proposal_accept_sends_the_reviewed_hash_as_given_and_never_reads_first(client) -> None:
    # ROOT CAUSE (security review, 2026-10-16): an accept that reads the proposal
    # and echoes that read's content_hash binds to whatever the proposal is at
    # accept time, so a proposal changed after review is accepted anyway. The
    # helper sends only the hash it is given, and none when it is given none.
    read = respx.get(f"{BASE}/v1/knowledge/wiki/proposals/x1").respond(
        200, json={"id": "x1", "content_hash": "cd" * 32}
    )
    accept = respx.post(f"{BASE}/v1/knowledge/wiki/proposals/x1/accept").respond(
        200, json={"status": "accepted", "proposal_id": "x1"}
    )

    client.knowledge.proposals.accept("x1", reviewed_hash="ab" * 32)
    client.knowledge.proposals.accept("x1")

    assert not read.called
    bodies = [call.request.content for call in accept.calls]
    assert json.loads(bodies[0]) == {"reviewed_hash": "ab" * 32}
    assert bodies[1] == b""
