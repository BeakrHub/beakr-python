"""The published SDK exposes only the documented core.

The documented contract is the core; the routes that left it stay reachable
("unlisted"), and this package carries no code for them (the Beakr CLI keeps a
private client for the few it calls). These tests pin that, and the request
shapes of the methods added for the new core operations.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

import beakr_sdk
from beakr_sdk import AsyncBeakr, Beakr

from .conftest import BASE

#: An external user's user_id (a uuid): external users are never named by external_id.
EXTERNAL_USER = "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f"

#: The public client's resources: one per documented area.
CORE_RESOURCES = {
    "bulk_uploads",
    "conversations",
    "end_user_tokens",
    "engine",
    "external_users",
    "files",
    "folders",
    "integrations",
    "interviews",
    "jobs",
    "keys",
    "knowledge",
    "labels",
    "me",
    "members",
    "meta",
    "org",
    "organizations",
    "projects",
    "runs",
    "threads",
}

#: Resources over unlisted routes only: on the private client, never the public one.
UNLISTED_RESOURCES = {
    "access",
    "activity",
    "billing",
    "connectors",
    "desktop",
    "documents",
    "invitations",
    "memories",
    "notifications",
    "outputs",
    "presentation_templates",
    "profile",
    "sharing",
    "skills",
    "templates",
    "trace",
    "usage",
    "workflows",
}


def _resources(client: object) -> set[str]:
    return {
        name
        for name, value in vars(client).items()
        if not name.startswith("_") and hasattr(value, "_client")
    }


def test_the_public_client_has_exactly_the_core_resources() -> None:
    with Beakr(api_key="bk_live_test", base_url=BASE) as beakr:
        assert _resources(beakr) - {"org"} == CORE_RESOURCES - {"org"}
        assert not (UNLISTED_RESOURCES & set(vars(beakr)))
        assert set(vars(beakr.org)) == {"service_accounts"}


def test_the_async_client_has_the_same_surface() -> None:
    sync = Beakr(api_key="bk_live_test", base_url=BASE)
    asynchronous = AsyncBeakr(api_key="bk_live_test", base_url=BASE)
    assert _resources(sync) == _resources(asynchronous)
    sync.close()


def test_unlisted_methods_are_not_on_public_resources() -> None:
    with Beakr(api_key="bk_live_test", base_url=BASE) as beakr:
        for resource, method in [
            ("files", "versions"),
            ("files", "preview"),
            ("knowledge", "undo_run"),
            ("knowledge", "changes"),
            ("projects", "trash"),
            ("projects", "restore"),
            ("integrations", "sync"),
            ("interviews", "send_request"),
            ("threads", "rate_message"),
        ]:
            assert not hasattr(getattr(beakr, resource), method), f"{resource}.{method}"
        assert not hasattr(beakr.knowledge.pages, "blame")
        assert not hasattr(beakr.projects, "sources")
        assert not hasattr(beakr.files, "bulk")


def test_the_public_api_layer_is_the_documented_contract_only() -> None:
    from beakr_sdk._generated.operations import OPERATIONS

    with Beakr(api_key="bk_live_test", base_url=BASE) as beakr:
        areas = {name for name in vars(beakr.api) if not name.startswith("_")}
    assert areas == {dotted.split(".")[0] for dotted in OPERATIONS}


def test_the_package_exports_nothing_private() -> None:
    assert not any("Internal" in name for name in beakr_sdk.__all__)
    assert "_internal" not in beakr_sdk.__all__
    assert not (Path(beakr_sdk.__file__).parent / "_internal").exists()


# -- the methods over the new core operations ---------------------------------------


@respx.mock
def test_end_user_token_mint_list_and_revoke(client) -> None:
    mint = respx.post(f"{BASE}/v1/auth/end-user-tokens").respond(
        201, json={"token": "bk_at_x", "token_id": "t1"}
    )
    listed = respx.get(f"{BASE}/v1/auth/end-user-tokens").respond(
        200, json={"data": [{"token_id": "t1"}], "next_cursor": None, "has_more": False}
    )
    revoke = respx.delete(f"{BASE}/v1/auth/end-user-tokens/t1").respond(204)

    minted = client.end_user_tokens.mint(
        subject="customer-42", project_ids=["p1"], scopes=["kb:read"], ttl_seconds=600
    )
    assert minted["token_id"] == "t1"
    assert json.loads(mint.calls.last.request.content) == {
        "subject": "customer-42",
        "project_ids": ["p1"],
        "scopes": ["kb:read"],
        "ttl_seconds": 600,
    }
    # A retry would mint a second token, so no Idempotency-Key is sent.
    assert "Idempotency-Key" not in mint.calls.last.request.headers

    assert client.end_user_tokens.list(subject="customer-42") == [{"token_id": "t1"}]
    assert listed.calls.last.request.url.params["subject"] == "customer-42"
    client.end_user_tokens.revoke("t1")
    assert revoke.called


@respx.mock
def test_end_user_token_for_an_external_user(client) -> None:
    mint = respx.post(f"{BASE}/v1/auth/end-user-tokens").respond(201, json={"token_id": "t2"})
    client.end_user_tokens.mint(external_user_id=EXTERNAL_USER, scopes=["kb:read", "agent:run"])
    assert json.loads(mint.calls.last.request.content) == {
        "external_user_id": EXTERNAL_USER,
        "scopes": ["kb:read", "agent:run"],
    }


@respx.mock
def test_integration_tools_and_call_tool(client) -> None:
    tools = respx.get(f"{BASE}/v1/integrations/tools").respond(
        200,
        json={
            "data": [{"name": "slack_search", "provider": "slack"}],
            "next_cursor": None,
            "has_more": False,
        },
    )
    call = respx.post(f"{BASE}/v1/integrations/tools/slack_search/call").respond(
        200, json={"tool": "slack_search", "content": "found", "is_error": False}
    )

    assert client.integrations.tools(provider="slack") == [
        {"name": "slack_search", "provider": "slack"}
    ]
    assert tools.calls.last.request.url.params["provider"] == "slack"

    result = client.integrations.call_tool("slack_search", {"query": "assay"}, connector_id="c1")
    assert result["content"] == "found"
    assert json.loads(call.calls.last.request.content) == {
        "arguments": {"query": "assay"},
        "connector_id": "c1",
    }


@respx.mock
def test_call_tool_without_arguments_sends_an_empty_object(client) -> None:
    call = respx.post(f"{BASE}/v1/integrations/tools/gmail_recent/call").respond(200, json={})
    client.integrations.call_tool("gmail_recent")
    assert json.loads(call.calls.last.request.content) == {"arguments": {}}


@respx.mock
def test_interview_link_in_one_call_result_and_send_to_projects(client) -> None:
    create = respx.post(f"{BASE}/v1/interview/links").respond(
        201, json={"link_id": "l1", "url": "https://beakr.test/i/x"}
    )
    result = respx.get(f"{BASE}/v1/interview/sendables/links/l1/result").respond(
        200, json={"link_id": "l1", "status": "completed"}
    )
    send = respx.post(f"{BASE}/v1/interview/sendables/links/l1/knowledge").respond(
        200, json=[{"project_id": "p1"}]
    )

    link = client.interviews.create_link(
        "Assay handover",
        goal="How the binding assay is run",
        questions=["Controls?"],
        recipient_name="Ada",
        expires_at="2026-11-01T00:00:00Z",
    )
    assert link["url"].startswith("https://")
    assert json.loads(create.calls.last.request.content) == {
        "title": "Assay handover",
        "goal": "How the binding assay is run",
        "questions": ["Controls?"],
        "recipient": {"name": "Ada"},
        "expires_at": "2026-11-01T00:00:00Z",
    }
    assert client.interviews.link_result("l1")["status"] == "completed"
    assert result.called
    client.interviews.send_to_projects("l1", ["p1", "p2"])
    assert json.loads(send.calls.last.request.content) == {"project_ids": ["p1", "p2"]}


@respx.mock
def test_interview_link_without_a_recipient_sends_none(client) -> None:
    create = respx.post(f"{BASE}/v1/interview/links").respond(201, json={"link_id": "l1"})
    client.interviews.create_link("Assay handover", questions=["Controls?"])
    assert json.loads(create.calls.last.request.content) == {
        "title": "Assay handover",
        "questions": ["Controls?"],
    }


@respx.mock
def test_external_users_resolve_the_active_org(client) -> None:
    respx.get(f"{BASE}/v1/me/organization").respond(200, json={"id": "org1"})
    create = respx.post(f"{BASE}/v1/organizations/org1/external-users").respond(
        201, json={"user_id": "u1", "created": True}
    )
    person = client.external_users.create("customer-42", name="Ada", metadata={"plan": "pro"})
    assert person["user_id"] == "u1"
    assert json.loads(create.calls.last.request.content) == {
        "external_id": "customer-42",
        "name": "Ada",
        "metadata": {"plan": "pro"},
    }


@respx.mock
def test_run_events_and_active_organization_are_on_the_public_client(client) -> None:
    # Two documented operations agent A found without SDK methods.
    respx.get(f"{BASE}/v1/me/organization").respond(200, json={"id": "org1"})
    respx.get(f"{BASE}/v1/runs/r1/events").mock(
        return_value=httpx.Response(
            200,
            text='id: 1\ndata: {"type": "stream_complete", "data": {}}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )
    assert client.me.active_org()["id"] == "org1"
    assert [event["type"] for event in client.runs.stream("r1")] == ["stream_complete"]


# -- many files in one batch (/v1/bulk) ------------------------------------------------


def _mock_bulk(storage: str = "https://bucket.s3.test") -> dict:
    def prepare(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "batch_id": "b1",
                "expires_at": "2026-10-12T13:00:00Z",
                "folder_paths": [],
                "uploads": [
                    {
                        "path": f["path"],
                        "key": f"k/{f['path']}",
                        "url": f"{storage}/{f['path']}?sig=1",
                        "headers": {},
                    }
                    for f in body["files"]
                ],
            },
        )

    return {
        "prepare": respx.post(f"{BASE}/v1/bulk/prepare").mock(side_effect=prepare),
        "put": respx.put(url__startswith=storage).respond(200),
        "complete": respx.post(f"{BASE}/v1/bulk/complete").respond(
            200,
            json={
                "batch_id": "b1",
                "folders": [],
                "artifacts": [{"id": "a1"}, {"id": "a2"}],
                "failed": [],
                "indexing_task_id": None,
                "job_id": "j1",
                "status_url": "/v1/jobs/j1",
            },
        ),
    }


@respx.mock
def test_upload_many_sends_files_and_folders_as_one_batch(client, tmp_path) -> None:
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "a.md").write_text("a")
    (tmp_path / "docs" / "sub" / "b.csv").write_text("x,y")
    (tmp_path / "docs" / ".hidden").write_text("skip")
    (tmp_path / "notes.txt").write_text("hello")
    routes = _mock_bulk()

    result = client.files.upload_many(
        [tmp_path / "docs", tmp_path / "notes.txt"], project_id="p1", folder_id="f1"
    )

    assert result["job_id"] == "j1"
    prepared = json.loads(routes["prepare"].calls.last.request.content)
    assert prepared["target_project_id"] == "p1"
    assert prepared["parent_folder_id"] == "f1"
    assert [f["path"] for f in prepared["files"]] == ["a.md", "sub/b.csv", "notes.txt"]
    assert all("authorization" not in c.request.headers for c in routes["put"].calls)
    completed = json.loads(routes["complete"].calls.last.request.content)
    assert completed["batch_id"] == "b1"
    assert completed["ingest_to_wiki"] is True
    assert [f["key"] for f in completed["files"]] == ["k/a.md", "k/sub/b.csv", "k/notes.txt"]
    assert completed["files"][1]["artifact_type"] == "dataset"


@respx.mock
def test_upload_many_can_wait_for_indexing(client, tmp_path) -> None:
    (tmp_path / "a.md").write_text("a")
    _mock_bulk()
    respx.get(f"{BASE}/v1/jobs/j1").respond(200, json={"id": "j1", "status": "succeeded"})
    result = client.files.upload_many(tmp_path / "a.md", project_id="p1", wait=True)
    assert result["job"]["status"] == "succeeded"


@respx.mock
def test_batch_status_reads_the_batch(client) -> None:
    route = respx.get(f"{BASE}/v1/bulk/b1/status").respond(
        200, json={"batch_id": "b1", "status": "indexing", "artifacts": [], "summary": {}}
    )
    assert client.files.batch_status("b1")["status"] == "indexing"
    assert route.called


def test_upload_many_refuses_two_files_with_one_name(client, tmp_path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.md").write_text("1")
    (tmp_path / "a.md").write_text("2")
    import pytest

    with pytest.raises(ValueError):
        client.files.upload_many([tmp_path / "x", tmp_path / "a.md"], project_id="p1")


def test_upload_many_refuses_more_than_one_batch(client, tmp_path, monkeypatch) -> None:
    import pytest

    from beakr_sdk.resources import _upload

    monkeypatch.setattr(_upload, "MAX_BATCH_FILES", 1)
    (tmp_path / "a.md").write_text("1")
    (tmp_path / "b.md").write_text("2")
    with respx.mock(assert_all_called=False) as mock:
        anything = mock.route().respond(200, json={})
        with pytest.raises(ValueError):
            client.files.upload_many(tmp_path, project_id="p1")
    assert not anything.called
