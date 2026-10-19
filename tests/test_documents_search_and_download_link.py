"""``knowledge.documents_search`` and the one-step download by reference."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from beakr_sdk import AsyncBeakr, Beakr

from .conftest import BASE


@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.asyncio
async def test_documents_search_sends_the_search_command_and_its_options(asynchronous):
    results = [{"source_ref": "beakr-source:v1:key:x.f1", "passages": []}]
    with respx.mock:
        route = respx.post("https://api.test/v1/knowledge/documents").mock(
            return_value=Response(
                200, json={"content": "1 doc", "sources": {}, "results": results, "is_error": False}
            )
        )
        if asynchronous:
            async with AsyncBeakr(api_key="test", base_url="https://api.test") as client:
                result = await client.knowledge.documents_search(
                    "who indemnifies whom", limit=5, passages=3, glob="*.pdf", project_id="Deals"
                )
        else:
            with Beakr(api_key="test", base_url="https://api.test") as client:
                result = client.knowledge.documents_search(
                    "who indemnifies whom", limit=5, passages=3, glob="*.pdf", project_id="Deals"
                )
    assert result["results"] == results
    assert json.loads(route.calls[0].request.content) == {
        "command": "search",
        "arguments": {"query": "who indemnifies whom", "limit": 5, "passages": 3, "glob": "*.pdf"},
        "project_id": "Deals",
    }


@respx.mock
def test_download_link_signs_any_reference_in_one_request(client) -> None:
    route = respx.post(f"{BASE}/v1/artifacts/download-url").respond(
        200,
        json={
            "url": "https://storage.test/k",
            "expires_in": 120,
            "filename": "memo.pdf",
            "mime_type": "application/pdf",
            "file": {
                "id": "f1",
                "version_id": "v1",
                "title": "memo.pdf",
                "display_path": "memo.pdf",
            },
        },
    )
    signed = client.files.download_link("beakr-source:v1:key:cmFnOjE.f1", inline=True)
    assert signed["url"] == "https://storage.test/k"
    assert json.loads(route.calls.last.request.content) == {
        "reference": "beakr-source:v1:key:cmFnOjE.f1",
        "inline": True,
    }


@respx.mock
def test_download_reference_saves_the_bytes_without_the_credential(client, tmp_path) -> None:
    respx.post(f"{BASE}/v1/artifacts/download-url").respond(
        200,
        json={
            "url": "https://storage.test/k",
            "expires_in": 120,
            "filename": "memo.pdf",
            "file": {
                "id": "f1",
                "version_id": "v1",
                "title": "memo.pdf",
                "display_path": "memo.pdf",
            },
        },
    )
    fetched = respx.get("https://storage.test/k").respond(200, content=b"%PDF")
    path = client.files.download_reference("beakr-source:v1:x", tmp_path)
    assert path == tmp_path / "memo.pdf" and path.read_bytes() == b"%PDF"
    assert "authorization" not in fetched.calls.last.request.headers
