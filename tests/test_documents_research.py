import json

import pytest
import respx
from httpx import Response

from beakr_sdk import AsyncBeakr, Beakr


@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.asyncio
async def test_documents_preserves_scope_and_read_bounds(asynchronous):
    with respx.mock:
        route = respx.post("https://api.test/v1/knowledge/documents").mock(
            return_value=Response(200, json={"content": "window", "sources": {}, "is_error": False})
        )
        if asynchronous:
            async with AsyncBeakr(api_key="test", base_url="https://api.test") as client:
                result = await client.knowledge.documents(
                    "read", project_id="project", document="memo", start_line=21, end_line=40
                )
        else:
            with Beakr(api_key="test", base_url="https://api.test") as client:
                result = client.knowledge.documents(
                    "read", project_id="project", document="memo", start_line=21, end_line=40
                )
    assert result["content"] == "window"
    assert json.loads(route.calls[0].request.content) == {
        "command": "read",
        "arguments": {"document": "memo", "start_line": 21, "end_line": 40},
        "project_id": "project",
    }


@pytest.mark.asyncio
async def test_research_preserves_server_metadata_in_both_clients():
    data = {
        "text": "finding",
        "citations": [],
        "checked": "one file",
        "not_checked": "others",
        "stopped_early": True,
        "cost_usd": None,
        "lead_model": "gpt-5.6-sol",
    }
    with respx.mock:
        respx.post("https://api.test/v1/knowledge/research").mock(
            return_value=Response(200, json=data)
        )
        with Beakr(api_key="test", base_url="https://api.test") as client:
            assert client.knowledge.research("question") == data
        async with AsyncBeakr(api_key="test", base_url="https://api.test") as client:
            assert await client.knowledge.research("question") == data


@pytest.mark.parametrize("timeout, expected", [(None, 170.0), (220.0, 220.0)])
def test_research_waits_for_server_and_preserves_longer_client_timeout(timeout, expected):
    with respx.mock:
        route = respx.post("https://api.test/v1/knowledge/research").mock(
            return_value=Response(200, json={"text": "ok"})
        )
        with Beakr(
            api_key="test",
            base_url="https://api.test",
            **({"timeout": timeout} if timeout is not None else {}),
        ) as client:
            client.knowledge.research("q")
    assert route.calls[0].request.extensions["timeout"]["read"] == expected


@pytest.mark.asyncio
async def test_quick_research_sends_quick_and_waits_less_in_both_clients():
    with respx.mock:
        route = respx.post("https://api.test/v1/knowledge/research").mock(
            return_value=Response(200, json={"text": "ok", "engine": "quick"})
        )
        with Beakr(api_key="test", base_url="https://api.test", timeout=5.0) as client:
            assert client.knowledge.research("q", quick=True)["engine"] == "quick"
        async with AsyncBeakr(api_key="test", base_url="https://api.test") as client:
            await client.knowledge.research("q", quick=True)
        with Beakr(api_key="test", base_url="https://api.test") as client:
            client.knowledge.research("q")
    sent = [json.loads(call.request.content) for call in route.calls]
    assert sent == [{"query": "q", "quick": True}, {"query": "q", "quick": True}, {"query": "q"}]
    assert route.calls[0].request.extensions["timeout"]["read"] == 30.0
