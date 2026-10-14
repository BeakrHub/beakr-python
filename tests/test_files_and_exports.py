"""Uploads go through the resumable ``/v1/uploads`` flow; downloads sign the current version."""

from __future__ import annotations

import hashlib
import json

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
    assert len(routes["folder"].calls) == 1
    assert json.loads(routes["folder"].calls.last.request.content) == {
        "name": "deep",
        "project_id": "p1",
    }
    assert [c.get("parent_folder_id") for c in routes["created"]] == [None, "fold-deep"]


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
