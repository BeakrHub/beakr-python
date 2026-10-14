"""Every file goes through the resumable multipart flow (``/v1/uploads``, spec 3.5).

Since the documented-core cut (2026-10-12) a small file is a one-part upload
too, so no upload touches the unlisted presigned-url / artifact / version routes.

Mocked HTTP: the flow is checked call by call -- what it declares, which parts
it sends, that part bytes go to the presigned URL without a Beakr credential,
that it resumes from the parts S3 already holds, and that a transient failure
surfaces the upload id to resume with.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr, BeakrUploadInterrupted

API = "https://api.test"
S3 = "https://s3.test/bucket"
PART = 10


@pytest.fixture
def big_file(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(bytes(range(25)))  # three parts of 10, 10 and 5 bytes
    return path


def _routes(uploaded: list[dict] | None = None) -> dict:
    sent: dict[int, bytes] = {}

    def presign(request):
        numbers = json.loads(request.content)["part_numbers"]
        return httpx.Response(
            200,
            json={
                "upload_id": "u1",
                "parts": [
                    {
                        "part_number": n,
                        "url": f"{S3}/u1?part={n}",
                        "expires_at": "2026-10-09T00:00:00Z",
                    }
                    for n in numbers
                ],
            },
        )

    def put(request):
        number = int(request.url.params["part"])
        sent[number] = request.content
        assert "authorization" not in request.headers
        return httpx.Response(200, headers={"ETag": f'"etag-{number}"'})

    state = {
        "id": "u1",
        "status": "open",
        "project_id": "p1",
        "content_length": 25,
        "part_size": PART,
        "part_count": 3,
        "uploaded_parts": uploaded or [],
    }
    return {
        "sent": sent,
        "create": respx.post(f"{API}/v1/uploads").respond(201, json=state),
        "get": respx.get(f"{API}/v1/uploads/u1").respond(200, json=state),
        "parts": respx.post(f"{API}/v1/uploads/u1/parts").mock(side_effect=presign),
        "put": respx.put(url__startswith=S3).mock(side_effect=put),
        "complete": respx.post(f"{API}/v1/uploads/u1/complete").respond(
            200,
            json={
                "upload": {**state, "status": "completed"},
                "artifact": {"id": "a1", "version_id": "v1"},
                "folders": [],
                "job_id": "j1",
                "status_url": "/v1/bulk/u1/status",
            },
        ),
    }


@respx.mock
def test_a_large_file_is_sent_in_parts(big_file) -> None:
    routes = _routes()
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        result = client.files.upload(big_file, project_id="p1", ingest=False)
    assert result["job_id"] == "j1"
    created = json.loads(routes["create"].calls[0].request.content)
    assert created == {
        "target_project_id": "p1",
        "path": "scan.pdf",
        "content_type": "application/pdf",
        "content_length": 25,
        "content_hash": hashlib.sha256(bytes(range(25))).hexdigest(),
        "ingest_to_wiki": False,
    }
    assert routes["sent"] == {1: bytes(range(10)), 2: bytes(range(10, 20)), 3: bytes(range(20, 25))}
    completed = json.loads(routes["complete"].calls[0].request.content)
    assert completed["parts"] == [{"part_number": n, "etag": f'"etag-{n}"'} for n in (1, 2, 3)]
    assert completed["title"] == "scan.pdf"


def _one_part(content: bytes, *, url: str = f"{S3}/u1?part=1") -> dict:
    state = {
        "id": "u1",
        "status": "open",
        "project_id": "p1",
        "content_length": len(content),
        "part_size": 5 * 1024 * 1024,
        "part_count": 1,
        "uploaded_parts": [],
    }
    return {
        "create": respx.post(f"{API}/v1/uploads").respond(201, json=state),
        "get": respx.get(f"{API}/v1/uploads/u1").respond(200, json=state),
        "parts": respx.post(f"{API}/v1/uploads/u1/parts").respond(
            200, json={"upload_id": "u1", "parts": [{"part_number": 1, "url": url}]}
        ),
        "put": respx.put(url__startswith=S3).respond(200, headers={"ETag": '"etag-1"'}),
        "complete": respx.post(f"{API}/v1/uploads/u1/complete").respond(
            200,
            json={
                "upload": {**state, "status": "completed"},
                "artifact": {"id": "a1"},
                "folders": [],
                "job_id": "j1",
                "status_url": "/v1/jobs/j1",
            },
        ),
    }


@respx.mock
def test_a_small_file_is_a_one_part_upload(tmp_path) -> None:
    # Root cause: files under 64 MB went through the presigned-url, artifact and
    # version routes, which are not in the documented contract.
    path = tmp_path / "small.txt"
    path.write_text("hi")
    routes = _one_part(b"hi")
    legacy = respx.post(f"{API}/v1/uploads/presigned-url")
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        result = client.files.upload(path, project_id="p1", folder_id="f1", ingest=False)
    assert result["artifact"]["id"] == "a1"
    assert not legacy.called
    created = json.loads(routes["create"].calls[0].request.content)
    assert created["target_project_id"] == "p1"
    assert created["parent_folder_id"] == "f1"
    assert created["ingest_to_wiki"] is False
    assert routes["put"].calls[0].request.content == b"hi"


@respx.mock
def test_a_file_with_no_project_goes_to_the_personal_project(tmp_path) -> None:
    path = tmp_path / "note.md"
    path.write_text("hi")
    respx.get(f"{API}/v1/projects").respond(
        200,
        json={
            "data": [
                {"id": "p-team", "project_type": "standard"},
                {"id": "p-me", "project_type": "personal"},
            ],
            "next_cursor": None,
            "has_more": False,
        },
    )
    routes = _one_part(b"hi")
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.upload(path)
    assert json.loads(routes["create"].calls[0].request.content)["target_project_id"] == "p-me"


def test_an_empty_file_is_refused_before_anything_is_sent(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    with respx.mock(assert_all_called=False) as mock:
        anything = mock.route().respond(200, json={})
        with Beakr(api_key="bk_live_test", base_url=API) as client:
            with pytest.raises(ValueError):
                client.files.upload(path, project_id="p1")
    assert not anything.called


@respx.mock
def test_resuming_sends_only_the_parts_s3_lacks(big_file) -> None:
    routes = _routes(uploaded=[{"part_number": 1, "size": PART, "etag": '"held-1"'}])
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.resume_upload("u1", big_file)
    assert not routes["create"].called
    assert sorted(routes["sent"]) == [2, 3]
    completed = json.loads(routes["complete"].calls[0].request.content)
    assert completed["parts"][0] == {"part_number": 1, "etag": '"held-1"'}


@respx.mock
def test_a_lost_connection_names_the_upload_to_resume(big_file) -> None:
    routes = _routes()
    routes["put"].mock(side_effect=httpx.ConnectError("reset"))
    with Beakr(api_key="bk_live_test", base_url=API, max_retries=0) as client:
        with pytest.raises(BeakrUploadInterrupted) as raised:
            client.files.upload(big_file, project_id="p1")
    assert raised.value.upload_id == "u1"
    assert "u1" in str(raised.value)


@respx.mock
def test_a_refusal_resuming_cannot_fix_is_not_reported_as_an_interruption(big_file) -> None:
    routes = _routes()
    routes["complete"].respond(
        422, json={"error": {"code": "size_mismatch", "message": "wrong size"}}
    )
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        with pytest.raises(Exception) as raised:
            client.files.upload(big_file, project_id="p1")
    assert not isinstance(raised.value, BeakrUploadInterrupted)


@respx.mock
def test_a_resumed_file_of_another_size_is_refused(big_file) -> None:
    _routes()
    big_file.write_bytes(b"x" * 30)
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        with pytest.raises(ValueError, match="25-byte file"):
            client.files.resume_upload("u1", big_file)


@respx.mock
def test_the_async_client_runs_the_same_flow(big_file) -> None:
    routes = _routes()

    async def run():
        async with AsyncBeakr(api_key="bk_live_test", base_url=API) as client:
            return await client.files.upload(big_file, project_id="p1")

    assert asyncio.run(run())["job_id"] == "j1"
    assert sorted(routes["sent"]) == [1, 2, 3]


@respx.mock
def test_the_uploads_resource_maps_each_route() -> None:
    created = respx.post(f"{API}/v1/uploads").respond(201, json={"id": "u1"})
    got = respx.get(f"{API}/v1/uploads/u1").respond(200, json={"id": "u1"})
    parts = respx.post(f"{API}/v1/uploads/u1/parts").respond(200, json={"parts": []})
    done = respx.post(f"{API}/v1/uploads/u1/complete").respond(200, json={})
    aborted = respx.delete(f"{API}/v1/uploads/u1").respond(200, json={"status": "aborted"})
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        uploads = client.files.uploads
        uploads.create(project_id="p1", path="a.pdf", content_length=9, ingest=False)
        uploads.get("u1")
        uploads.presign_parts("u1", [1, 2])
        uploads.complete("u1")
        uploads.abort("u1")
    assert json.loads(created.calls[0].request.content)["ingest_to_wiki"] is False
    assert created.calls[0].request.headers.get("idempotency-key")
    assert json.loads(parts.calls[0].request.content) == {"part_numbers": [1, 2]}
    assert json.loads(done.calls[0].request.content) == {}
    assert got.called and aborted.called


@respx.mock
def test_a_presigned_url_keeps_its_signed_query_string(tmp_path) -> None:
    # Root cause: the request builder always passed ``params`` to httpx, which
    # replaces the URL's query string even when ``params`` is empty, so the
    # X-Amz-* signature of every presigned upload URL was dropped.
    path = tmp_path / "small.txt"
    path.write_text("hi")
    routes = _one_part(b"hi", url=f"{S3}/k?X-Amz-Signature=abc&X-Amz-Expires=300")
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.upload(path, project_id="p1")
    assert routes["put"].calls[0].request.url.params["X-Amz-Signature"] == "abc"
