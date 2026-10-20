"""Skills: the one filtered list, a skill by id or slug, its files, the library and proposals.

``client.skills`` over ``/v1/skills``, ``/v1/me/skills/library`` and
``/v1/me/skills/proposals``, on both clients.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
import respx

from beakr_sdk.errors import ConflictError
from beakr_sdk.resources.skills import bundle_sha256, read_skill_folder

from .conftest import BASE

SKILL = {
    "id": "s1",
    "slug": "plate-reader",
    "name": "Plate reader",
    "description": "Read plate-reader exports.",
    "domain": None,
    "visibility": "private",
    "owner": "me",
    "origin": "imported",
    "installed": True,
    "created_at": "2026-10-20T00:00:00Z",
    "updated_at": "2026-10-20T00:00:00Z",
    "files": None,
}
FILES = {"SKILL.md": "---\nname: plate-reader\n---\nRead it.\n", "scripts/run.py": "print(1)\n"}
BUNDLE = {
    "skill_id": "s1",
    "slug": "plate-reader",
    "files": FILES,
    "file_count": 2,
    "files_sha256": bundle_sha256(FILES),
}


def _page(rows, cursor=None):
    return {"data": rows, "next_cursor": cursor, "has_more": cursor is not None}


@respx.mock
def test_list_sends_every_filter_and_follows_every_page(client) -> None:
    route = respx.get(f"{BASE}/v1/skills").mock(
        side_effect=[
            respx.MockResponse(200, json=_page([SKILL], cursor="c2")),
            respx.MockResponse(200, json=_page([{**SKILL, "id": "s2"}])),
        ]
    )
    skills = client.skills.list(
        visibility=["private", "org"],
        owner="me",
        origin=("created", "imported"),
        installed=False,
        search="plate",
        domain="lab",
    )
    assert [s["id"] for s in skills] == ["s1", "s2"]
    first = route.calls[0].request.url.params
    assert first.get_list("visibility") == ["private", "org"]
    assert first.get_list("owner") == ["me"]
    assert first.get_list("origin") == ["created", "imported"]
    assert (first["installed"], first["search"], first["domain"]) == ("false", "plate", "lab")
    assert "include_content" not in first
    assert route.calls[1].request.url.params["cursor"] == "c2"


@respx.mock
def test_list_without_filters_sends_none(client) -> None:
    route = respx.get(f"{BASE}/v1/skills").respond(200, json=_page([]))
    assert client.skills.list() == []
    assert set(route.calls.last.request.url.params) == {"limit"}


@respx.mock
def test_get_takes_an_id_or_a_slug(client) -> None:
    route = respx.get(f"{BASE}/v1/skills/plate-reader").respond(
        200, json={**SKILL, "latest_revision": None}
    )
    assert client.skills.get("plate-reader", include_content=True)["owner"] == "me"
    assert route.calls.last.request.url.params["include_content"] == "true"


@respx.mock
def test_download_writes_the_folder_inside_an_existing_directory(client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(200, json=BUNDLE)
    saved = client.skills.download("s1", tmp_path)
    assert saved == tmp_path / "plate-reader"
    assert (saved / "scripts" / "run.py").read_text() == "print(1)\n"
    assert read_skill_folder(saved) == FILES


@respx.mock
def test_download_as_zip(client, tmp_path) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        for name, text in FILES.items():
            zipped.writestr(f"plate-reader/{name}", text)
    respx.get(f"{BASE}/v1/skills/s1/bundle.zip").respond(
        200, content=archive.getvalue(), headers={"content-type": "application/zip"}
    )
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(200, json=BUNDLE)
    saved = client.skills.download("s1", tmp_path, as_zip=True)
    assert saved == tmp_path / "plate-reader.zip"
    # Beakr's zip holds the folder; reading it back gives the skill's files.
    assert read_skill_folder(saved) == FILES


@respx.mock
def test_download_refuses_a_path_outside_the_folder(client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(
        200, json={**BUNDLE, "files": {"SKILL.md": "x", "../escape.md": "no"}}
    )
    with pytest.raises(ValueError, match="leaves the skill folder"):
        client.skills.download("s1", tmp_path / "out")
    assert not (tmp_path / "escape.md").exists()


@respx.mock
def test_create_from_folder_posts_its_text_files(client, tmp_path) -> None:
    folder = tmp_path / "plate-reader"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(FILES["SKILL.md"])
    (folder / "scripts" / "run.py").write_text(FILES["scripts/run.py"])
    (folder / ".DS_Store").write_bytes(b"\x00\x01")
    route = respx.post(f"{BASE}/v1/skills").respond(201, json=SKILL)
    client.skills.create_from_folder(folder, visibility="org", source="claude-code")
    assert json.loads(route.calls.last.request.content) == {
        "files": FILES,
        "visibility": "org",
        "source": "claude-code",
    }


@respx.mock
def test_create_from_a_zip_and_refusals(client, tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("SKILL.md", FILES["SKILL.md"])
        zipped.writestr("__MACOSX/._SKILL.md", b"\x00")
    route = respx.post(f"{BASE}/v1/skills").respond(201, json=SKILL)
    client.skills.create_from_folder(archive)
    assert json.loads(route.calls.last.request.content)["files"] == {"SKILL.md": FILES["SKILL.md"]}

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no SKILL.md"):
        client.skills.create_from_folder(empty)
    binary = tmp_path / "binary"
    binary.mkdir()
    (binary / "SKILL.md").write_text("x")
    (binary / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff")
    with pytest.raises(ValueError, match="logo.png is not UTF-8 text"):
        client.skills.create_from_folder(binary)
    assert route.call_count == 1


@respx.mock
def test_upload_bundle_sends_the_base_digest_and_raises_on_a_conflict(client, tmp_path) -> None:
    route = respx.put(f"{BASE}/v1/skills/s1/bundle").mock(
        side_effect=[
            respx.MockResponse(200, json=BUNDLE),
            respx.MockResponse(
                409, json={"error": {"code": "skill_conflict", "message": "changed"}}
            ),
        ]
    )
    client.skills.upload_bundle("s1", FILES, expected_sha256="abc", replace=False)
    assert json.loads(route.calls.last.request.content) == {
        "files": FILES,
        "replace": False,
        "expected_sha256": "abc",
    }
    folder = tmp_path / "plate-reader"
    folder.mkdir()
    (folder / "SKILL.md").write_text(FILES["SKILL.md"])
    with pytest.raises(ConflictError) as caught:
        client.skills.upload_bundle("s1", folder, force=True)
    assert caught.value.code == "skill_conflict"
    assert json.loads(route.calls.last.request.content) == {
        "files": {"SKILL.md": FILES["SKILL.md"]},
        "replace": True,
        "force": True,
    }


@respx.mock
def test_library_and_delete(client) -> None:
    installed = respx.post(f"{BASE}/v1/me/skills/library/s1").respond(
        201, json={"status": "installed", "skill_id": "s1"}
    )
    removed = respx.delete(f"{BASE}/v1/me/skills/library/s1").respond(204)
    deleted = respx.delete(f"{BASE}/v1/skills/s1").respond(204)
    assert client.skills.install("s1")["status"] == "installed"
    client.skills.uninstall("s1")
    client.skills.delete("s1")
    assert installed.called and removed.called and deleted.called


@respx.mock
def test_proposals_list_accept_and_dismiss(client) -> None:
    listed = respx.get(f"{BASE}/v1/me/skills/proposals").respond(
        200, json=_page([{"id": "p1", "title": "Plate reader", "status": "pending"}])
    )
    accepted = respx.post(f"{BASE}/v1/me/skills/proposals/p1/accept").respond(201, json=SKILL)
    dismissed = respx.post(f"{BASE}/v1/me/skills/proposals/p2/dismiss").respond(
        200, json={"status": "dismissed", "proposal_id": "p2"}
    )
    assert [p["id"] for p in client.skills.proposals.list(status="pending")] == ["p1"]
    assert listed.calls.last.request.url.params["status"] == "pending"
    client.skills.proposals.accept("p1", force=True)
    assert accepted.calls.last.request.url.params["force"] == "true"
    assert client.skills.proposals.dismiss("p2")["status"] == "dismissed"
    assert dismissed.called


def test_bundle_sha256_matches_the_servers_digest() -> None:
    # The engine's bundle_files_sha256 for these two files.
    assert bundle_sha256({"b.md": "two", "a.md": "one"}) == bundle_sha256(
        {"a.md": "one", "b.md": "two"}
    )
    assert bundle_sha256({}) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@respx.mock
async def test_async_list_and_download(async_client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/skills").respond(200, json=_page([SKILL]))
    respx.get(f"{BASE}/v1/skills/s1/bundle").respond(200, json=BUNDLE)
    assert [s["slug"] for s in await async_client.skills.list(owner="me")] == ["plate-reader"]
    saved = await async_client.skills.download("s1", tmp_path)
    assert (saved / "SKILL.md").read_text() == FILES["SKILL.md"]
