"""Every documented operation has a Python SDK method (C039).

Coverage is proven by behaviour, not by a name table: for each operation the
mapping below names the SDK call that serves it, the test runs that call
against a mocked transport, and asserts the request it actually sent is that
operation (method and path). A contract operation with no mapping, a mapping
whose call sends something else, or a mapped call that reaches a route outside
the contracts all fail.

The contract is the documented one (``public-openapi.json``): the published
SDK covers the whole documented core and sends nothing outside it. Routes the
API serves without documenting them belong to the Beakr CLI's private client,
which has its own coverage test over its allowlist.

The contract is read from the engine checkout, else the committed snapshot
(``tests._engine.engine_checkout``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from beakr_sdk import Beakr
from tests._engine import engine_checkout

#: An external user's user_id (a uuid): external users are never named by external_id.
EXTERNAL_USER = "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f"

BASE = "https://api.test"
_REPO = Path(__file__).resolve().parents[1]

ORG = "org1"
SKILL_FILES = {"SKILL.md": "---\nname: x\n---\nbody"}

# (contract operation, SDK call). Path params use fixed sample ids.
MAPPING: list[tuple[str, Callable[[Beakr], Any]]] = [
    ("GET /v1/artifacts", lambda c: c.files.list(project_id="p1")),
    ("DELETE /v1/artifacts/{}", lambda c: c.files.delete("a1")),
    ("GET /v1/artifacts/{}", lambda c: c.files.get("a1")),
    ("PATCH /v1/artifacts/{}", lambda c: c.files.update("a1", title="New")),
    ("GET /v1/artifacts/{}/content", lambda c: c.files.content("a1")),
    ("POST /v1/artifacts/read", lambda c: c.files.read("deals/memo.pdf", unit="p. 7")),
    ("POST /v1/artifacts/download-url", lambda c: c.files.download_link("beakr-source:v1:x")),
    ("POST /v1/artifacts/{}/knowledge", lambda c: c.files.send_to_knowledge("a1")),
    ("POST /v1/artifacts/{}/knowledge/approve", lambda c: c.files.approve_knowledge("a1")),
    ("POST /v1/artifacts/{}/move", lambda c: c.files.move("a1", "p2")),
    ("POST /v1/runs", lambda c: c.runs.start("hi", project_id="p1")),
    ("GET /v1/runs/{}", lambda c: c.runs.get("r1")),
    ("POST /v1/runs/{}/cancel", lambda c: c.runs.cancel("r1")),
    ("GET /v1/runs/{}/events", lambda c: list(c.runs.stream("r1"))),
    ("GET /v1/me/organization", lambda c: c.me.active_org()),
    ("GET /v1/me/threads", lambda c: c.threads.list()),
    ("GET /v1/projects/{}/threads", lambda c: c.threads.shared()),
    ("GET /v1/jobs/{}", lambda c: c.jobs.get("j1")),
    ("POST /v1/compound", lambda c: c.engine.compound("cv1")),
    ("POST /v1/compound/cancel", lambda c: c.engine.cancel_compound("cv1")),
    ("POST /v1/context", lambda c: c.engine.context(project_id="p1")),
    ("GET /v1/conversations", lambda c: c.conversations.list()),
    (
        "POST /v1/conversations/import",
        lambda c: c.conversations.import_(
            client="claude-code", session_id="s1", messages=[{"role": "user", "content": "x"}]
        ),
    ),
    ("GET /v1/conversations/search", lambda c: c.conversations.search("pump")),
    ("DELETE /v1/conversations/{}", lambda c: c.conversations.delete("cv1")),
    ("GET /v1/conversations/{}", lambda c: c.conversations.get("cv1")),
    ("GET /v1/folders", lambda c: c.folders.list("p1")),
    ("POST /v1/folders", lambda c: c.folders.create("Data", project_id="p1")),
    ("DELETE /v1/folders/{}", lambda c: c.folders.delete("f1")),
    ("GET /v1/folders/{}", lambda c: c.folders.get("f1")),
    ("PATCH /v1/folders/{}", lambda c: c.folders.update("f1", name="Raw")),
    ("GET /v1/folders/{}/resources", lambda c: c.folders.contents("f1")),
    ("GET /v1/integrations", lambda c: c.integrations.list()),
    ("DELETE /v1/integrations/{}", lambda c: c.integrations.disconnect("k1")),
    ("GET /v1/integrations/{}/status", lambda c: c.integrations.status("k1")),
    ("POST /v1/integrations/{}/connect", lambda c: c.integrations.connect("google-drive")),
    ("GET /v1/interview/sendables", lambda c: c.interviews.sendables()),
    ("POST /v1/interview/sendables", lambda c: c.interviews.create_sendable("Handover")),
    ("GET /v1/interview/sendables/links/{}/result", lambda c: c.interviews.link_result("l1")),
    ("POST /v1/interview/sendables/links/{}/revoke", lambda c: c.interviews.revoke_link("l1")),
    ("POST /v1/interview/sendables/{}/links", lambda c: c.interviews.link_for("s1")),
    (
        "POST /v1/interview/links",
        lambda c: c.interviews.create_link("Assay handover", goal="How the assay runs"),
    ),
    (
        "POST /v1/interview/sendables/links/{}/knowledge",
        lambda c: c.interviews.send_to_projects("l1", ["p1"]),
    ),
    ("GET /v1/integrations/tools", lambda c: c.integrations.tools(provider="slack")),
    (
        "POST /v1/integrations/tools/{}/call",
        lambda c: c.integrations.call_tool("slack_search", {"query": "assay"}),
    ),
    (
        "POST /v1/auth/end-user-tokens",
        lambda c: c.end_user_tokens.mint(subject="u1", project_ids=["p1"], scopes=["kb:read"]),
    ),
    ("GET /v1/auth/end-user-tokens", lambda c: c.end_user_tokens.list(subject="u1")),
    ("DELETE /v1/auth/end-user-tokens/{}", lambda c: c.end_user_tokens.revoke("t1")),
    (
        "GET /v1/organizations/{}/external-users",
        lambda c: c.external_users.list(org_id=ORG),
    ),
    (
        "POST /v1/organizations/{}/external-users",
        lambda c: c.external_users.create("customer-42", name="Ada", org_id=ORG),
    ),
    (
        "GET /v1/organizations/{}/external-users/{}",
        lambda c: c.external_users.get(EXTERNAL_USER, org_id=ORG),
    ),
    (
        "PATCH /v1/organizations/{}/external-users/{}",
        lambda c: c.external_users.update(EXTERNAL_USER, name="Ada L.", org_id=ORG),
    ),
    (
        "DELETE /v1/organizations/{}/external-users/{}",
        lambda c: c.external_users.delete(EXTERNAL_USER, org_id=ORG),
    ),
    (
        "POST /v1/knowledge/documents",
        lambda c: c.knowledge.documents("grep", project_id="p1", pattern="renewal"),
    ),
    ("POST /v1/knowledge/research", lambda c: c.knowledge.research("who owns billing?")),
    ("POST /v1/knowledge/wiki/command", lambda c: c.knowledge.command("ls", check=False)),
    ("GET /v1/knowledge/wiki/pages", lambda c: c.knowledge.pages.list()),
    ("GET /v1/knowledge/wiki/pages/by-title", lambda c: c.knowledge.pages.by_title("BK-7")),
    ("GET /v1/knowledge/wiki/pages/{}", lambda c: c.knowledge.pages.get("pg1")),
    ("GET /v1/knowledge/wiki/pages/{}/revisions", lambda c: c.knowledge.pages.revisions("pg1")),
    ("GET /v1/knowledge/wiki/pages/{}/sources", lambda c: c.knowledge.pages.sources("pg1")),
    ("GET /v1/knowledge/agent-review", lambda c: c.knowledge.agent_review.get()),
    (
        "PUT /v1/knowledge/agent-review",
        lambda c: c.knowledge.agent_review.set(agent_proposals_need_a_person=True),
    ),
    (
        "PUT /v1/knowledge/agent-review/projects/{}",
        lambda c: c.knowledge.agent_review.set_project("p1", agent_uploads_need_a_person=True),
    ),
    ("GET /v1/knowledge/wiki/proposals", lambda c: c.knowledge.proposals.list()),
    (
        "POST /v1/knowledge/wiki/proposals",
        lambda c: c.knowledge.proposals.create("archive", rationale="dup", page="Old"),
    ),
    ("GET /v1/knowledge/wiki/proposals/{}", lambda c: c.knowledge.proposals.show("wp1")),
    ("POST /v1/knowledge/wiki/proposals/{}/accept", lambda c: c.knowledge.proposals.accept("wp1")),
    (
        "POST /v1/knowledge/wiki/proposals/{}/dismiss",
        lambda c: c.knowledge.proposals.dismiss("wp1"),
    ),
    ("GET /v1/knowledge/wiki/search", lambda c: c.knowledge.pages.search("binding")),
    (
        "POST /v1/knowledge/wiki/write",
        lambda c: c.knowledge.write("archive", check=False, page="Old", rationale="dup"),
    ),
    ("GET /v1/me", lambda c: c.me.get()),
    ("GET /v1/me/api-keys", lambda c: c.keys.list()),
    ("POST /v1/me/api-keys", lambda c: c.keys.create(name="ci", scopes=["kb:read"])),
    ("GET /v1/me/api-keys/scopes", lambda c: c.keys.scopes()),
    ("DELETE /v1/me/api-keys/{}", lambda c: c.keys.revoke("key1")),
    ("GET /v1/organizations", lambda c: c.organizations.list()),
    ("GET /v1/organizations/{}", lambda c: c.organizations.get(ORG)),
    ("GET /v1/organizations/{}/members", lambda c: c.members.list(org_id=ORG)),
    ("POST /v1/organizations/{}/members", lambda c: c.members.add("u1", org_id=ORG)),
    ("DELETE /v1/organizations/{}/members/{}", lambda c: c.members.remove("u1", org_id=ORG)),
    (
        "PATCH /v1/organizations/{}/members/{}",
        lambda c: c.members.set_role("u1", "admin", org_id=ORG),
    ),
    (
        "GET /v1/organizations/{}/service-accounts",
        lambda c: c.org.service_accounts.list(org_id=ORG),
    ),
    (
        "POST /v1/organizations/{}/service-accounts",
        lambda c: c.org.service_accounts.create("CI", org_id=ORG),
    ),
    (
        "POST /v1/organizations/{}/service-accounts/{}/disable",
        lambda c: c.org.service_accounts.disable("sa1", org_id=ORG),
    ),
    (
        "POST /v1/organizations/{}/service-accounts/{}/keys",
        lambda c: c.org.service_accounts.mint_key("sa1", scopes=["kb:read"], org_id=ORG),
    ),
    ("GET /v1/projects", lambda c: c.projects.list()),
    ("POST /v1/projects", lambda c: c.projects.create("Platform")),
    ("DELETE /v1/projects/{}", lambda c: c.projects.delete("p1")),
    ("GET /v1/projects/{}", lambda c: c.projects.get("p1")),
    ("PATCH /v1/projects/{}", lambda c: c.projects.update("p1", name="x")),
    ("POST /v1/projects/{}/invite", lambda c: c.projects.invite("p1", "ada@example.com")),
    ("GET /v1/projects/{}/members", lambda c: c.projects.members("p1")),
    ("POST /v1/projects/{}/members", lambda c: c.projects.add_member("p1", "u1")),
    ("DELETE /v1/projects/{}/members/{}/{}", lambda c: c.projects.remove_member("p1", "u1")),
    ("GET /v1/projects/{}/pending-invitations", lambda c: c.projects.pending_invitations("p1")),
    ("GET /v1/projects/{}/knowledge-summary", lambda c: c.projects.knowledge_status("p1")),
    (
        "GET /v1/projects/{}/knowledge/failed-files",
        lambda c: c.projects.knowledge_failures("p1"),
    ),
    ("POST /v1/projects/{}/knowledge/retry-failed", lambda c: c.projects.retry_failed("p1")),
    ("GET /v1/projects/{}/knowledge-profile", lambda c: c.projects.knowledge_profile("p1")),
    (
        "PUT /v1/projects/{}/knowledge-profile",
        lambda c: c.projects.set_knowledge_profile("p1", name="Basel"),
    ),
    (
        "PATCH /v1/projects/{}/knowledge-profile",
        lambda c: c.projects.update_knowledge_profile("p1", name="Basel"),
    ),
    (
        "DELETE /v1/projects/{}/pending-invitations/{}",
        lambda c: c.projects.revoke_invitation("p1", "i1"),
    ),
    ("GET /v1/public/openapi.json", lambda c: c.meta.openapi()),
    ("GET /v1/threads/{}", lambda c: c.threads.detail("t1")),
    (
        "POST /v1/bulk/complete",
        lambda c: c.bulk_uploads.complete("b1", [{"path": "a.pdf", "key": "k", "title": "a"}]),
    ),
    ("POST /v1/bulk/prepare", lambda c: c.bulk_uploads.prepare("p1", [{"path": "a.pdf"}])),
    (
        "POST /v1/uploads",
        lambda c: c.files.uploads.create(project_id="p1", path="a.bin", content_length=10),
    ),
    ("GET /v1/uploads/{}", lambda c: c.files.uploads.get("u1")),
    ("POST /v1/uploads/{}/parts", lambda c: c.files.uploads.presign_parts("u1", [1])),
    ("POST /v1/uploads/{}/complete", lambda c: c.files.uploads.complete("u1")),
    ("DELETE /v1/uploads/{}", lambda c: c.files.uploads.abort("u1")),
    ("GET /v1/bulk/{}/status", lambda c: c.bulk_uploads.status("b1")),
    ("GET /v1/skills", lambda c: c.skills.list(owner="me")),
    ("POST /v1/skills", lambda c: c.skills.create(SKILL_FILES)),
    ("GET /v1/skills/{}", lambda c: c.skills.get("s1")),
    ("DELETE /v1/skills/{}", lambda c: c.skills.delete("s1")),
    ("GET /v1/skills/{}/bundle", lambda c: c.skills.bundle("s1")),
    ("PUT /v1/skills/{}/bundle", lambda c: c.skills.upload_bundle("s1", SKILL_FILES)),
    ("GET /v1/skills/{}/bundle.zip", lambda c: c.skills.download("s1", "x.zip", as_zip=True)),
    ("POST /v1/me/skills/library/{}", lambda c: c.skills.install("s1")),
    ("DELETE /v1/me/skills/library/{}", lambda c: c.skills.uninstall("s1")),
    ("GET /v1/me/skills/proposals", lambda c: c.skills.proposals.list()),
    ("POST /v1/me/skills/proposals/{}/accept", lambda c: c.skills.proposals.accept("sp1")),
    ("POST /v1/me/skills/proposals/{}/dismiss", lambda c: c.skills.proposals.dismiss("sp1")),
    ("GET /v1/labels", lambda c: c.labels.list()),
    ("POST /v1/labels", lambda c: c.labels.create("Grants")),
    ("DELETE /v1/labels/{}", lambda c: c.labels.delete("lb1")),
    ("GET /v1/labels/{}", lambda c: c.labels.get("lb1")),
    ("PATCH /v1/labels/{}", lambda c: c.labels.update("lb1", name="Grants 2026")),
    ("GET /v1/labels/{}/members", lambda c: c.labels.members("lb1")),
    ("POST /v1/labels/{}/members", lambda c: c.labels.add_member("lb1", "u1")),
    ("DELETE /v1/labels/{}/members/{}", lambda c: c.labels.remove_member("lb1", "u1")),
    ("GET /v1/labels/{}/projects", lambda c: c.labels.projects("lb1")),
    ("POST /v1/labels/{}/projects", lambda c: c.labels.attach_project("lb1", "p1")),
    ("DELETE /v1/labels/{}/projects/{}", lambda c: c.labels.detach_project("lb1", "p1")),
    (
        "PATCH /v1/labels/{}/projects/{}",
        lambda c: c.labels.set_project_role("lb1", "p1", "viewer"),
    ),
    ("GET /v1/artifacts/versions/{}/download-url", lambda c: c.files.download_url("a1")),
]


def _normalize(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def _contract_operations(*, include_deprecated: bool = True) -> set[str]:
    """Documented operations; a deprecated path kept for older clients (it names its
    ``x-beakr-canonical-path``) needs no SDK method of its own -- the SDK calls the
    canonical path that replaces it."""
    spec = json.loads(engine_checkout("docs/api/public-openapi.json").read_text())
    return {
        f"{method.upper()} {_normalize(path)}"
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if include_deprecated or "x-beakr-canonical-path" not in operation
    }


def _replacements() -> dict[str, str]:
    """Canonical operation -> the deprecated operation whose handler it runs."""
    spec = json.loads(engine_checkout("docs/api/public-openapi.json").read_text())
    return {
        f"{method.upper()} {_normalize(path)}": (
            f"{method.upper()} {_normalize(op['x-beakr-replaces'])}"
        )
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if "x-beakr-replaces" in op
    }


def _matches(operation: str, method: str, path: str) -> bool:
    want_method, template = operation.split(" ", 1)
    pattern = "^" + re.escape(template).replace(r"\{\}", "[^/]+") + "$"
    return method == want_method and re.match(pattern, path) is not None


def _respond(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/stream") or re.fullmatch(r"/v1/runs/[^/]+/events", path):
        body = 'id: 1\ndata: {"type": "stream_complete", "data": {}}\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    payload = {
        "id": "x1",
        "org_id": ORG,
        "status": "ok",
        "data": [],
        "has_more": False,
        "content": [{"type": "text", "text": "ok"}],
        "outcome": "success",
        "issuer": "https://issuer.test",
        "client_id": "cli",
        "status_url": "/v1/project-imports/im1",
        "current_version_id": "v1",
        "url": "https://storage.test/object",
        "key": "uploads/k",
    }
    return httpx.Response(200, json=payload)


def _client(cls: type[Beakr]) -> Beakr:
    beakr = cls(api_key="bk_live_test", base_url=BASE)
    beakr._sleep = lambda _seconds: None
    return beakr


def test_every_contract_operation_has_a_mapped_sdk_call():
    required = _contract_operations(include_deprecated=False)
    mapped = {operation for operation, _call in MAPPING}
    # A canonical path and the deprecated path it replaces run one handler; an
    # SDK method on either serves the operation.
    covered = mapped | {
        canonical for canonical, legacy in _replacements().items() if legacy in mapped
    }
    assert sorted(required - covered) == [], "documented operations without an SDK method"
    assert sorted(mapped - _contract_operations()) == [], (
        "mapping names operations not in the contract"
    )


def test_mapping_has_no_duplicates():
    names = [operation for operation, _call in MAPPING]
    assert len(names) == len(set(names))


@pytest.mark.parametrize(("operation", "call"), MAPPING, ids=[m[0] for m in MAPPING])
def test_sdk_call_sends_its_operation(operation, call):
    client = _client(Beakr)
    operations = _contract_operations()
    try:
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            route = router.route().mock(side_effect=_respond)
            call(client)
    finally:
        client.close()
    sent = [(r.request.method, r.request.url.path) for r in route.calls]
    assert any(_matches(operation, method, path) for method, path in sent), (operation, sent)
    for method, path in sent:
        key = f"{method} {_normalize(path)}"
        assert any(_matches(op, method, path) for op in operations), (
            f"{operation} reached a route outside the documented contract: {key}"
        )
