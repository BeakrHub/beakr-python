"""Names the server sends never carry a download outside the chosen directory.

Root cause (security review of beakr-python#60): ``skills.download`` built
``target / f"{slug}.zip"`` and ``target / slug`` from the bundle's slug, and the
file downloads kept ``Path(name).name`` of the server's file name, which is
``..`` for ``".."``. A slug of ``"../x"``, ``"/abs"``, ``"a/b"``, ``".."``,
``"."`` or ``""``, or a file named ``".."``, wrote outside the directory (or onto
it). Every server-supplied name is now one checked component, and every write
path must resolve inside the destination; otherwise ``UnsafePathError``.
"""

from __future__ import annotations

import io
import zipfile

import pytest
import respx

from beakr_sdk import BeakrError, UnsafePathError
from beakr_sdk.resources._destination import contained, file_name, safe_component

from .conftest import BASE

S3 = "https://storage.test"
FILES = {"SKILL.md": "---\nname: x\n---\nRun it.\n"}


def _tree(root):
    """Every path under ``root``: a refused download must leave it unchanged."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


UNSAFE_SLUGS = ["../x", "/abs", "a/b", "..", ".", "", "a\\b", "C:evil", "x\0y"]


@pytest.mark.parametrize("slug", UNSAFE_SLUGS)
def test_safe_component_refuses_each_traversal_shape(slug) -> None:
    with pytest.raises(UnsafePathError):
        safe_component(slug, what="the skill slug")


def test_unsafe_path_error_is_a_beakr_error_and_a_value_error() -> None:
    assert issubclass(UnsafePathError, BeakrError)
    assert issubclass(UnsafePathError, ValueError)


def test_file_name_keeps_the_last_component_and_refuses_dot_dot() -> None:
    assert file_name("reports/q3.pdf", what="the file name") == "q3.pdf"
    assert file_name("a\\b\\memo.pdf", what="the file name") == "memo.pdf"
    for name in ["..", "reports/..", "", "/", "."]:
        with pytest.raises(UnsafePathError):
            file_name(name, what="the file name")


def test_contained_refuses_a_symlink_that_points_outside(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        contained(root, "link/SKILL.md", what="the skill file path")


@respx.mock
@pytest.mark.parametrize("slug", UNSAFE_SLUGS)
def test_skill_folder_download_refuses_an_unsafe_slug(client, tmp_path, slug) -> None:
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(
        200, json={"skill_id": "s1", "slug": slug, "files": FILES}
    )
    root = tmp_path / "root"
    root.mkdir()
    before = _tree(tmp_path)
    with pytest.raises(UnsafePathError):
        client.skills.download("s1", root)
    assert _tree(tmp_path) == before


@respx.mock
@pytest.mark.parametrize("slug", UNSAFE_SLUGS)
def test_skill_zip_download_refuses_an_unsafe_slug(client, tmp_path, slug) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("x/SKILL.md", FILES["SKILL.md"])
    respx.get(f"{BASE}/v1/skills/s1/bundle.zip").respond(
        200, content=archive.getvalue(), headers={"content-type": "application/zip"}
    )
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(
        200, json={"skill_id": "s1", "slug": slug, "files": FILES}
    )
    root = tmp_path / "root"
    root.mkdir()
    before = _tree(tmp_path)
    with pytest.raises(UnsafePathError):
        client.skills.download("s1", root, as_zip=True)
    assert _tree(tmp_path) == before


@respx.mock
@pytest.mark.parametrize("path", ["../escape.md", "/etc/x", "C:/x", "a/../../x", "a\0b"])
def test_skill_file_paths_stay_inside_the_folder(client, tmp_path, path) -> None:
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(
        200, json={"skill_id": "s1", "slug": "ok", "files": {**FILES, path: "no"}}
    )
    with pytest.raises(UnsafePathError):
        client.skills.download("s1", tmp_path)
    assert not (tmp_path / "ok").exists()


@respx.mock
@pytest.mark.parametrize("name", ["..", "reports/.."])
def test_download_reference_refuses_an_unsafe_file_name(client, tmp_path, name) -> None:
    respx.post(f"{BASE}/v1/artifacts/download-url").respond(
        200, json={"url": f"{S3}/k", "filename": name, "file": {"id": "f1"}}
    )
    respx.get(f"{S3}/k").respond(200, content=b"%PDF")
    root = tmp_path / "root"
    root.mkdir()
    before = _tree(tmp_path)
    with pytest.raises(UnsafePathError):
        client.files.download_reference("beakr-source:v1:x", root)
    assert _tree(tmp_path) == before


@respx.mock
@pytest.mark.parametrize("title", ["..", "a/..", "."])
def test_download_refuses_an_unsafe_title(client, tmp_path, title) -> None:
    respx.get(f"{BASE}/v1/artifacts/a1").respond(
        json={"id": "a1", "title": title, "current_version_id": "v9"}
    )
    respx.get(f"{BASE}/v1/artifacts/versions/v9/download-url").respond(json={"url": f"{S3}/obj"})
    respx.get(f"{S3}/obj").respond(200, content=b"%PDF")
    root = tmp_path / "root"
    root.mkdir()
    before = _tree(tmp_path)
    with pytest.raises(UnsafePathError):
        client.files.download("a1", root)
    assert _tree(tmp_path) == before


@respx.mock
def test_a_missing_file_name_falls_back_to_the_reference_inside(client, tmp_path) -> None:
    respx.post(f"{BASE}/v1/artifacts/download-url").respond(
        200, json={"url": f"{S3}/k", "filename": "", "file": {"id": "f1"}}
    )
    respx.get(f"{S3}/k").respond(200, content=b"%PDF")
    root = tmp_path / "root"
    root.mkdir()
    path = client.files.download_reference("../ref", root)
    assert path == root / "ref"
