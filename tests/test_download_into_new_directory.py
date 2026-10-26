"""A download to ``"dir/"`` that does not exist yet saves inside a new ``dir``.

Root cause (sandbox e2e, 2026-10-25): every download resolved its destination
with ``Path(destination).is_dir()``. ``Path`` drops a trailing ``/``, and a
directory that does not exist yet is not a directory, so the README quickstart's
first ``download_reference(ref, "downloads/")`` wrote the file itself as
``./downloads`` and the next download into ``"downloads/ref/"`` failed with
``FileExistsError``. A destination ending in a separator now names a directory.
"""

from __future__ import annotations

import io
import os
import zipfile

import respx

from beakr_sdk.resources._destination import names_directory
from beakr_sdk.resources.skills import read_skill_folder

from .conftest import BASE

S3 = "https://storage.test"
FILES = {"SKILL.md": "---\nname: plate-reader\n---\nRun it.\n", "scripts/run.py": "print(1)\n"}
BUNDLE = {"skill_id": "s1", "slug": "plate-reader", "files": FILES}


def test_a_trailing_separator_names_a_directory_that_does_not_exist_yet(tmp_path) -> None:
    missing = tmp_path / "downloads"
    assert names_directory(f"{missing}{os.sep}")
    assert names_directory(tmp_path)
    assert not names_directory(missing)
    assert not names_directory(str(missing / "memo.pdf"))


@respx.mock
def test_download_reference_creates_the_directory_and_keeps_the_name(client, tmp_path) -> None:
    respx.post(f"{BASE}/v1/artifacts/download-url").respond(
        200, json={"url": f"{S3}/k", "filename": "memo.pdf", "file": {"id": "f1"}}
    )
    respx.get(f"{S3}/k").respond(200, content=b"%PDF")
    path = client.files.download_reference("beakr-source:v1:x", f"{tmp_path / 'downloads'}/")
    assert path == tmp_path / "downloads" / "memo.pdf"
    assert path.read_bytes() == b"%PDF"
    # A second download into a nested new directory works too (it used to hit
    # FileExistsError because the first one had written a file named downloads).
    nested = client.files.download_reference("beakr-source:v1:x", f"{tmp_path / 'downloads'}/ref/")
    assert nested == tmp_path / "downloads" / "ref" / "memo.pdf"


@respx.mock
def test_download_creates_the_directory_and_keeps_the_name(client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/artifacts/a1").respond(
        json={"id": "a1", "title": "Report.pdf", "current_version_id": "v9"}
    )
    respx.get(f"{BASE}/v1/artifacts/versions/v9/download-url").respond(json={"url": f"{S3}/obj"})
    respx.get(f"{S3}/obj").respond(200, content=b"%PDF")
    path = client.files.download("a1", f"{tmp_path / 'out'}/")
    assert path == tmp_path / "out" / "Report.pdf"
    assert path.read_bytes() == b"%PDF"


@respx.mock
def test_download_to_a_new_file_path_still_writes_that_file(client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/artifacts/a1").respond(
        json={"id": "a1", "title": "Report.pdf", "current_version_id": "v9"}
    )
    respx.get(f"{BASE}/v1/artifacts/versions/v9/download-url").respond(json={"url": f"{S3}/obj"})
    respx.get(f"{S3}/obj").respond(200, content=b"%PDF")
    path = client.files.download("a1", str(tmp_path / "renamed.pdf"))
    assert path == tmp_path / "renamed.pdf"


@respx.mock
def test_skill_folder_download_into_a_new_directory(client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(200, json=BUNDLE)
    saved = client.skills.download("s1", f"{tmp_path / 'exported'}/")
    assert saved == tmp_path / "exported" / "plate-reader"
    assert read_skill_folder(saved) == FILES


@respx.mock
def test_skill_zip_download_into_a_new_directory_gets_a_zip_name(client, tmp_path) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, text in FILES.items():
            zipped.writestr(f"plate-reader/{name}", text)
    respx.get(f"{BASE}/v1/skills/s1/bundle.zip").respond(
        200, content=archive.getvalue(), headers={"content-type": "application/zip"}
    )
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(200, json=BUNDLE)
    saved = client.skills.download("s1", f"{tmp_path / 'zips'}/", as_zip=True)
    assert saved == tmp_path / "zips" / "plate-reader.zip"
    assert read_skill_folder(saved) == FILES
