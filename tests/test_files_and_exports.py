"""Uploads go through the resumable ``/v1/uploads`` flow; downloads sign the current version."""

from __future__ import annotations

import hashlib
import json

import httpx
import respx

from beakr_sdk.resources._upload import detect_artifact_type
from tests.upload_routes import S3, mock_resumable_uploads

from .conftest import BASE


@respx.mock
def test_upload_one_file(client, tmp_path) -> None:
    routes = mock_resumable_uploads(BASE)
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    result = client.files.upload(path, project_id="p1", folder_id="f1")

    assert result["artifact"]["id"] == "art-notes.txt"
    assert routes["created"] == [
        {
            "target_project_id": "p1",
            "parent_folder_id": "f1",
            "path": "notes.txt",
            "content_type": "text/plain",
            "content_length": 5,
            "content_hash": hashlib.sha256(b"hello").hexdigest(),
            "ingest_to_wiki": True,
        }
    ]
    put = routes["put"].calls.last.request
    assert put.content == b"hello"
    assert "authorization" not in put.headers
    completed = json.loads(routes["complete"].calls.last.request.content)
    assert completed["title"] == "notes.txt"
    assert completed["artifact_type"] == "doc"


@respx.mock
def test_upload_folder_mirrors_subdirectories_and_skips_dotfiles(client, tmp_path) -> None:
    routes = mock_resumable_uploads(BASE)
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "a.md").write_text("a")
    (tmp_path / "docs" / "deep" / "b.md").write_text("b")
    (tmp_path / "docs" / ".secret").write_text("x")
    (tmp_path / "docs" / ".git").mkdir()
    (tmp_path / "docs" / ".git" / "HEAD").write_text("x")

    results = client.files.upload_folder(tmp_path / "docs", project_id="p1")

    assert [r["artifact"]["id"] for r in results] == ["art-a.md", "art-b.md"]
    # The tree starts with a folder named after the directory, as each file's
    # recorded path does (docs/a.md, docs/deep/b.md).
    assert [json.loads(c.request.content) for c in routes["folder"].calls] == [
        {"name": "docs", "project_id": "p1"},
        {"name": "deep", "project_id": "p1", "parent_folder_id": "fold-docs"},
    ]
    assert [c.get("parent_folder_id") for c in routes["created"]] == ["fold-docs", "fold-deep"]


@respx.mock
def test_upload_folder_without_root_folder_fills_the_target_directly(client, tmp_path) -> None:
    routes = mock_resumable_uploads(BASE)
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "a.md").write_text("a")
    (tmp_path / "docs" / "deep" / "b.md").write_text("b")

    client.files.upload_folder(tmp_path / "docs", project_id="p1", root_folder=False)

    assert [json.loads(c.request.content)["name"] for c in routes["folder"].calls] == ["deep"]
    assert [c.get("parent_folder_id") for c in routes["created"]] == [None, "fold-deep"]


@respx.mock
def test_upload_folder_reuses_folders_that_already_exist(client, tmp_path) -> None:
    routes = mock_resumable_uploads(
        BASE,
        folders=[
            {"id": "have-docs", "name": "docs", "parent_folder_id": None},
            {"id": "have-deep", "name": "deep", "parent_folder_id": "have-docs"},
        ],
    )
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "a.md").write_text("a")
    (tmp_path / "docs" / "deep" / "b.md").write_text("b")

    client.files.upload_folder(tmp_path / "docs", project_id="p1")

    assert not routes["folder"].called
    assert [c.get("parent_folder_id") for c in routes["created"]] == ["have-docs", "have-deep"]


@respx.mock
def test_two_trees_with_the_same_subfolder_names_upload_side_by_side(client, tmp_path) -> None:
    # Regression: each tree's subfolders were created at the project root and an
    # existing folder of the same name was never reused, so the second matter's
    # "Closing" answered 400 halfway through, leaving a partial upload.
    routes = mock_resumable_uploads(BASE)
    for matter in ("1005-00001", "1001-00004"):
        (tmp_path / matter / "Closing").mkdir(parents=True)
        (tmp_path / matter / "Closing" / f"{matter}.md").write_text(matter)

    client.files.upload_folder(tmp_path / "1005-00001", project_id="p1")
    client.files.upload_folder(tmp_path / "1001-00004", project_id="p1")

    tree = {(f["name"], f.get("parent_folder_id")) for f in routes["folders"]}
    assert tree == {
        ("1005-00001", None),
        ("Closing", "fold-1005-00001"),
        ("1001-00004", None),
        ("Closing", "fold-1001-00004"),
    }
    assert [c.get("parent_folder_id") for c in routes["created"]] == [
        "fold-Closing",
        "fold-Closing+",
    ]


@respx.mock
def test_uploading_the_same_tree_again_reuses_its_folders(client, tmp_path) -> None:
    routes = mock_resumable_uploads(BASE)
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "b.md").write_text("b")

    client.files.upload_folder(tmp_path / "docs", project_id="p1")
    client.files.upload_folder(tmp_path / "docs", project_id="p1")

    assert len(routes["folder"].calls) == 2  # docs and deep, created once
    assert [c.get("parent_folder_id") for c in routes["created"]] == ["fold-deep", "fold-deep"]


@respx.mock
def test_a_folder_created_meanwhile_is_reused_after_the_api_refuses_a_duplicate(
    client, tmp_path
) -> None:
    routes = mock_resumable_uploads(BASE)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("a")
    listed = {"calls": 0}
    real_list = routes["list_folders"].side_effect

    def stale_then_fresh(request):
        # The first listing misses "docs"; someone creates it before our create.
        listed["calls"] += 1
        if listed["calls"] == 1:
            routes["folders"].append({"id": "theirs", "name": "docs", "parent_folder_id": None})
            return httpx.Response(200, json={"data": [], "next_cursor": None, "has_more": False})
        return real_list(request)

    routes["list_folders"].side_effect = stale_then_fresh

    client.files.upload_folder(tmp_path / "docs", project_id="p1")

    assert routes["folder"].calls.last.response.status_code == 400
    assert [c.get("parent_folder_id") for c in routes["created"]] == ["theirs"]


@respx.mock
def test_download_saves_bytes_without_sending_the_credential(client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/artifacts/a1").respond(
        json={"id": "a1", "title": "Report.pdf", "current_version_id": "v9"}
    )
    signed = respx.get(f"{BASE}/v1/artifacts/versions/v9/download-url").respond(
        json={"url": f"{S3}/obj?sig=1"}
    )
    blob = respx.get(f"{S3}/obj").respond(200, content=b"%PDF")
    path = client.files.download("a1", tmp_path)
    assert path == tmp_path / "Report.pdf"
    assert path.read_bytes() == b"%PDF"
    assert signed.called
    assert "authorization" not in blob.calls.last.request.headers


def test_artifact_type_matches_the_web_classifier() -> None:
    assert detect_artifact_type("image/png", "a.png") == "image"
    assert detect_artifact_type("text/csv", "a.csv") == "dataset"
    assert detect_artifact_type("application/octet-stream", "cells.h5ad") == "scientific_data"
    assert detect_artifact_type("application/octet-stream", "n.ipynb") == "notebook"
    assert detect_artifact_type("application/pdf", "a.pdf") == "doc"
