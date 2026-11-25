"""Who wrote a file and when, apart from who uploaded it (R11).

``authored_by`` and ``authored_at`` on ``POST /v1/uploads/{id}/complete``, on each
``files[]`` item of ``POST /v1/bulk/complete`` and on ``PATCH /v1/artifacts/{id}``
(beakr-engine fix/upload-authored-by); a citation of such a file carries
``written_by`` on its source record.
"""

from __future__ import annotations

import json

import respx

from beakr_sdk import Beakr
from beakr_sdk.sources import public_source

from .test_multipart_upload import API, _routes, big_file  # noqa: F401
from .test_upload_relative_paths import _mock_bulk

JANE = "Jane Doe <jane@example.com>"


@respx.mock
def test_upload_and_resume_send_them_on_complete(big_file) -> None:  # noqa: F811
    routes = _routes()
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.upload(big_file, project_id="p1", authored_by=JANE, authored_at="2026-06-14")
        client.files.resume_upload("u1", big_file, authored_at="2026-06-14T09:30:00Z")
        client.files.uploads.complete("u1", authored_by="")
    first, resumed, low_level = (json.loads(c.request.content) for c in routes["complete"].calls)
    assert (first["authored_by"], first["authored_at"]) == (JANE, "2026-06-14")
    assert resumed["authored_at"] == "2026-06-14T09:30:00Z" and "authored_by" not in resumed
    assert low_level == {"authored_by": ""}
    created = json.loads(routes["create"].calls[0].request.content)
    assert "authored_by" not in created and "authored_at" not in created


@respx.mock
def test_a_batch_sends_each_files_own_author_over_the_default(tmp_path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "c.md").write_text("c")
    routes = _mock_bulk()
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.upload_many(
            [tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"],
            project_id="p1",
            authored_by=JANE,
            authored={
                tmp_path / "b.md": {"authored_by": "Sam Roe", "authored_at": "2026-05-01"},
                str(tmp_path / "c.md"): {"authored_at": "2026-04-30"},
            },
        )
    files = json.loads(routes["complete"].calls.last.request.content)["files"]
    assert [(f["path"], f.get("authored_by"), f.get("authored_at")) for f in files] == [
        ("a.md", JANE, None),
        ("b.md", "Sam Roe", "2026-05-01"),
        ("c.md", JANE, "2026-04-30"),
    ]


@respx.mock
def test_without_them_a_batch_sends_none(tmp_path) -> None:
    (tmp_path / "a.md").write_text("a")
    routes = _mock_bulk()
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.upload_many(tmp_path / "a.md", project_id="p1")
    (only,) = json.loads(routes["complete"].calls.last.request.content)["files"]
    assert "authored_by" not in only and "authored_at" not in only


@respx.mock
def test_update_sets_and_clears_them() -> None:
    route = respx.patch(f"{API}/v1/artifacts/a1").respond(200, json={"id": "a1"})
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        client.files.update("a1", authored_by=JANE, authored_at="2026-06-14")
        client.files.update("a1", authored_at="")
    first, cleared = (json.loads(c.request.content) for c in route.calls)
    assert first == {"authored_by": JANE, "authored_at": "2026-06-14"}
    assert cleared == {"authored_at": ""}


def test_a_source_record_keeps_who_wrote_the_file() -> None:
    record = public_source(
        {
            "source_ref": "file:a1",
            "title": "Assay notes",
            "written_by": {
                "name": "Jane Doe",
                "by_agent": False,
                "org_member": True,
                "text": "Written by Jane Doe",
                "user_id": "u-jane",
            },
        }
    )
    assert record["written_by"] == {
        "name": "Jane Doe",
        "by_agent": False,
        "org_member": True,
        "text": "Written by Jane Doe",
    }
