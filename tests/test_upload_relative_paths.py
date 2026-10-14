"""Uploads record where each file sat on the uploader's machine (``relative_path``).

Knowledge reads name a cited file by its recorded path (``location.display_path``)
and ``files.read`` resolves it, so ``deals/acme/memo.pdf#p. 7`` is the path the
uploader knows. Before this, a folder upload dropped the folder's own name and
``mirror_folders=False`` dropped every directory, so the path a reader saw was not
one the uploader could follow back to the file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from beakr_sdk.resources._upload import upload_relative_path
from tests.test_core_surface import _mock_bulk
from tests.upload_routes import mock_resumable_uploads

from .conftest import BASE


def _completed(routes: dict) -> list[dict]:
    return [json.loads(call.request.content) for call in routes["complete"].calls]


def _tree(root: Path) -> None:
    (root / "docs" / "guides").mkdir(parents=True)
    (root / "docs" / "top.md").write_text("t")
    (root / "docs" / "guides" / "a.md").write_text("a")


@respx.mock
@pytest.mark.parametrize("mirror", [True, False])
def test_folder_upload_records_paths_from_the_folders_parent(client, tmp_path, mirror) -> None:
    routes = mock_resumable_uploads(BASE)
    _tree(tmp_path)

    client.files.upload_folder(tmp_path / "docs", project_id="p1", mirror_folders=mirror)

    # Resumable uploads carry it on complete, never on create.
    assert all("relative_path" not in body for body in routes["created"])
    assert [body.get("relative_path") for body in _completed(routes)] == [
        "docs/guides/a.md",
        "docs/top.md",
    ]


@respx.mock
def test_folder_upload_of_the_current_directory_uses_its_name(
    client, tmp_path, monkeypatch
) -> None:
    routes = mock_resumable_uploads(BASE)
    _tree(tmp_path)
    monkeypatch.chdir(tmp_path / "docs")

    client.files.upload_folder(".", project_id="p1")

    assert [body.get("relative_path") for body in _completed(routes)] == [
        "docs/guides/a.md",
        "docs/top.md",
    ]


@respx.mock
def test_single_file_records_a_relative_path_with_directories(
    client, tmp_path, monkeypatch
) -> None:
    routes = mock_resumable_uploads(BASE)
    (tmp_path / "deals" / "acme").mkdir(parents=True)
    (tmp_path / "deals" / "acme" / "memo.txt").write_text("m")
    (tmp_path / "plain.txt").write_text("p")
    monkeypatch.chdir(tmp_path)

    client.files.upload("deals/acme/memo.txt", project_id="p1")
    client.files.upload("plain.txt", project_id="p1")
    client.files.upload(tmp_path / "deals" / "acme" / "memo.txt", project_id="p1")

    assert [body.get("relative_path") for body in _completed(routes)] == [
        "deals/acme/memo.txt",
        None,
        None,
    ]


@respx.mock
def test_batch_upload_records_relative_paths(client, tmp_path, monkeypatch) -> None:
    _tree(tmp_path)
    (tmp_path / "deals").mkdir()
    (tmp_path / "deals" / "memo.txt").write_text("m")
    monkeypatch.chdir(tmp_path)
    routes = _mock_bulk()

    client.files.upload_many(["docs", "deals/memo.txt"], project_id="p1")

    completed = json.loads(routes["complete"].calls.last.request.content)
    assert [(f["path"], f.get("relative_path")) for f in completed["files"]] == [
        ("guides/a.md", "docs/guides/a.md"),
        ("top.md", "docs/top.md"),
        ("memo.txt", "deals/memo.txt"),
    ]


def test_relative_path_is_never_absolute_or_parent_relative(tmp_path) -> None:
    assert upload_relative_path(Path("../outside/memo.pdf")) is None
    assert upload_relative_path(Path("./memo.pdf")) is None
    assert upload_relative_path(tmp_path / "memo.pdf") is None
    assert upload_relative_path(Path("./deals/memo.pdf")) == "deals/memo.pdf"
