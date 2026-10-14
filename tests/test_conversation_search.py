"""SDK: conversation search and freshness (engine W4.4), on both clients."""

from __future__ import annotations

import respx

from .conftest import BASE

_HIT = {
    "kind": "imported",
    "id": "c1",
    "title": "Pump calibration",
    "summary": "March curve",
    "findings": [],
    "outputs": [],
    "href": "/v1/conversations/c1",
}


@respx.mock
def test_search_sends_query_and_options(client) -> None:
    route = respx.get(f"{BASE}/v1/conversations/search").respond(
        json={"data": [_HIT], "query": "pump"}
    )
    result = client.conversations.search(
        "pump", project_id="p1", include_threads=False, match="any", limit=5
    )
    assert result["data"][0]["id"] == "c1"
    params = route.calls.last.request.url.params
    assert dict(params) == {
        "q": "pump",
        "project_id": "p1",
        "include_threads": "false",
        "match": "any",
        "limit": "5",
    }


@respx.mock
def test_search_defaults_include_chats_and_match_all(client) -> None:
    route = respx.get(f"{BASE}/v1/conversations/search").respond(json={"data": []})
    client.conversations.search("pump")
    params = route.calls.last.request.url.params
    assert params["include_threads"] == "true" and params["match"] == "all"
    assert "project_id" not in params and "limit" not in params


@respx.mock
def test_import_sends_what_the_session_read(client) -> None:
    import json as _json

    route = respx.post(f"{BASE}/v1/conversations/import").respond(
        json={"conversation_id": "c1", "created": True}
    )
    reads = [{"page_id": "p-1", "revision": 3}, {"source_ref": "src_a", "version": "v2"}]
    client.conversations.import_(
        client="codex",
        session_id="s1",
        messages=[{"role": "user", "content": "hi"}],
        sources_read=reads,
    )
    assert _json.loads(route.calls.last.request.content)["sources_read"] == reads
