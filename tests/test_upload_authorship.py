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
        client.files.upload(
            big_file, project_id="p1", authored_by=[JANE, "Bob Smith"], authored_at="2026-06-14"
        )
        client.files.resume_upload("u1", big_file, authored_at="2026-06-14T09:30:00Z")
        client.files.uploads.complete("u1", authored_by="")
    first, resumed, low_level = (json.loads(c.request.content) for c in routes["complete"].calls)
    assert (first["authored_by"], first["authored_at"]) == ([JANE, "Bob Smith"], "2026-06-14")
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
                tmp_path / "b.md": {
                    "authored_by": ("Sam Roe", "Ana Li"),
                    "authored_at": "2026-05-01",
                },
                str(tmp_path / "c.md"): {"authored_at": "2026-04-30"},
            },
        )
    files = json.loads(routes["complete"].calls.last.request.content)["files"]
    assert [(f["path"], f.get("authored_by"), f.get("authored_at")) for f in files] == [
        ("a.md", JANE, None),
        ("b.md", ["Sam Roe", "Ana Li"], "2026-05-01"),
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
        client.files.update("a1", authored_by=[])
    first, cleared, no_authors = (json.loads(c.request.content) for c in route.calls)
    assert first == {"authored_by": JANE, "authored_at": "2026-06-14"}
    assert cleared == {"authored_at": ""}
    assert no_authors == {"authored_by": []}


def test_a_source_record_keeps_who_wrote_the_file() -> None:
    record = public_source(
        {
            "source_ref": "file:a1",
            "title": "Assay notes",
            "written_by": {
                "authors": [
                    {"name": "Jane Doe", "org_member": True, "user_id": "u-jane"},
                    {"name": "Bob Smith", "org_member": False},
                ],
                "by_agent": True,
                "text": "an agent for Jane Doe and Bob Smith",
            },
        }
    )
    assert record["written_by"] == {
        "authors": [
            {"name": "Jane Doe", "org_member": True},
            {"name": "Bob Smith", "org_member": False},
        ],
        "by_agent": True,
        "text": "an agent for Jane Doe and Bob Smith",
    }
    assert "written_by" not in public_source({"source_ref": "file:a1", "written_by": {}})


#: A ``GET /v1/knowledge/wiki/pages/{page_id}/sources`` row for a citation of a
#: co-written upload, as sandbox served it (engine 3a7bbde4fd, 26 Nov 2026),
#: trimmed. The route carries ``written_by`` as the compiler stored it in the
#: citation's ``meta``: a list of ``{name, user_id?}`` entries, not the public
#: ``SourceWrittenBy`` object a Knowledge read's source record carries.
PAGE_SOURCES_ROW = {
    "source_ref": "beakr-source:v1:file:b75b5c40-8bc3-4e13-85cc-2f1a99321da0",
    "key": "artifact:a6799af3-4b0f-49d0-98f8-847aa30d7fa8-u1",
    "source_type": "artifact",
    "source_title": "r11-coauthored-note.md",
    "section_id": "sec_description",
    "stance": "support",
    "meta": {
        "origin": "upload",
        "file_name": "r11-coauthored-note.md",
        "artifact_id": "b75b5c40-8bc3-4e13-85cc-2f1a99321da0",
        "written_by": [{"name": "Dr. Jane Testwell"}, {"name": "Bob Sample"}],
    },
    "artifact_id": "b75b5c40-8bc3-4e13-85cc-2f1a99321da0",
    "excerpt": "Assay R108-T2 was run by two people on 2025-04-02.",
    "written_by": [{"name": "Dr. Jane Testwell"}, {"name": "Bob Sample"}],
}


def test_a_page_sources_row_keeps_who_wrote_the_file() -> None:
    # Root cause: public_source read ``written_by`` only as the public
    # SourceWrittenBy dict, so the stored list a page sources row carries was
    # dropped and knowledge.pages.sources() never said who wrote a cited file.
    record = public_source(PAGE_SOURCES_ROW)
    assert record["written_by"] == {
        "authors": [
            {"name": "Dr. Jane Testwell", "org_member": False},
            {"name": "Bob Sample", "org_member": False},
        ],
        "by_agent": False,
        "text": "Dr. Jane Testwell and Bob Sample",
    }


def test_a_stamp_names_members_and_agents_but_never_an_id() -> None:
    stamp = [
        {"name": "Jane  Doe", "user_id": "u-jane", "by_agent": True},
        {"name": "Bob Smith", "by_agent": True},
        {"name": "Ana Li", "by_agent": True},
        {"name": "  "},
    ]
    record = public_source({"source_ref": "file:a1", "meta": {"written_by": stamp}})
    assert record["written_by"] == {
        "authors": [
            {"name": "Jane Doe", "org_member": True},
            {"name": "Bob Smith", "org_member": False},
            {"name": "Ana Li", "org_member": False},
        ],
        "by_agent": True,
        "text": "an agent for Jane Doe, Bob Smith and Ana Li",
    }
    assert "u-jane" not in json.dumps(record)
    # The one-author dict early dev builds stored, and an empty stamp.
    single = public_source({"source_ref": "file:a1", "written_by": {"name": "Jane Doe"}})
    assert single["written_by"]["text"] == "Jane Doe"
    assert "written_by" not in public_source({"source_ref": "file:a1", "written_by": []})


@respx.mock
def test_pages_sources_says_who_wrote_a_cited_file() -> None:
    respx.get(f"{API}/v1/knowledge/wiki/pages/pg1/sources").respond(
        200, json={"data": [PAGE_SOURCES_ROW], "next_cursor": None, "has_more": False}
    )
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        (record,) = client.knowledge.pages.sources("pg1")
    assert record["written_by"]["text"] == "Dr. Jane Testwell and Bob Sample"
