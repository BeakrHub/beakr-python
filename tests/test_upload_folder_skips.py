"""A folder upload skips a file the server refuses for itself and sends the rest.

``files.upload_folder`` once raised on the first per-file refusal (a ``.fcs``
answered 400 ``unsupported_file_type``) and never sent the files after it.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from beakr_sdk import AuthenticationError, PermissionDeniedError
from tests.upload_routes import mock_resumable_uploads

from .conftest import BASE

_FCS_MESSAGE = "File type not supported for 'b.fcs': application/octet-stream"


def _refuse(name: str, status: int, code: str, message: str, routes: dict) -> None:
    create = routes["create"].side_effect

    def answer(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["path"].endswith(name):
            return httpx.Response(status, json={"error": {"code": code, "message": message}})
        return create(request)

    routes["create"].side_effect = answer


def _run_folder(tmp_path):
    folder = tmp_path / "runfolder"
    folder.mkdir()
    (folder / "a.md").write_text("a")
    (folder / "b.fcs").write_bytes(b"FCS3.0")
    (folder / "c.md").write_text("c")
    return folder


@respx.mock
def test_unsupported_file_is_skipped_and_later_files_still_go(client, tmp_path) -> None:
    routes = mock_resumable_uploads(BASE)
    _refuse("b.fcs", 400, "unsupported_file_type", _FCS_MESSAGE, routes)

    result = client.files.upload_folder(_run_folder(tmp_path), project_id="p1")

    assert [row["artifact"]["id"] for row in result] == ["art-a.md", "art-c.md"]
    assert result.skipped == [
        {
            "path": str(tmp_path / "runfolder" / "b.fcs"),
            "name": "b.fcs",
            "code": "unsupported_file_type",
            "message": _FCS_MESSAGE,
        }
    ]
    assert result.total == 3
    assert result.summary == (f"Uploaded 2 of 3 files.\n- Failed: b.fcs: {_FCS_MESSAGE}")
    # Still the list it always was.
    assert isinstance(result, list)
    assert result.uploaded == list(result)


@respx.mock
async def test_async_client_skips_the_same_way(async_client, tmp_path) -> None:
    routes = mock_resumable_uploads(BASE)
    _refuse("b.fcs", 400, "unsupported_file_type", _FCS_MESSAGE, routes)

    result = await async_client.files.upload_folder(_run_folder(tmp_path), project_id="p1")

    assert [row["artifact"]["id"] for row in result] == ["art-a.md", "art-c.md"]
    assert [row["code"] for row in result.skipped] == ["unsupported_file_type"]


@respx.mock
def test_an_empty_file_is_skipped(client, tmp_path) -> None:
    mock_resumable_uploads(BASE)
    folder = _run_folder(tmp_path)
    (folder / "b.fcs").write_bytes(b"")

    result = client.files.upload_folder(folder, project_id="p1")

    assert [row["artifact"]["id"] for row in result] == ["art-a.md", "art-c.md"]
    assert [(row["name"], row["code"]) for row in result.skipped] == [("b.fcs", "empty_file")]


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (401, "unauthorized", AuthenticationError),
        (403, "forbidden", PermissionDeniedError),
    ],
)
@respx.mock
def test_a_refusal_of_the_whole_upload_still_raises(client, tmp_path, status, code, error) -> None:
    routes = mock_resumable_uploads(BASE)
    _refuse("b.fcs", status, code, "No", routes)

    with pytest.raises(error):
        client.files.upload_folder(_run_folder(tmp_path), project_id="p1")
    # Stopped there: c.md was never sent.
    assert [body["path"] for body in routes["created"]] == ["a.md"]
