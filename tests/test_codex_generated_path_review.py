"""A generated resource id must never select a different URL route."""

import httpx
import pytest

from beakr_sdk import AsyncBeakr, Beakr


@pytest.mark.parametrize("value", [".", ".."])
def test_generated_sync_path_rejects_dot_segments_before_sending(value):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        with Beakr(
            api_key="bk_test_fixture", base_url="https://api.test", http_client=transport
        ) as client:
            try:
                client.api.projects.get_project(value)
            except ValueError:
                pass
    assert not requests, f"Invalid project id sent a request to {requests[0].url}"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [".", ".."])
async def test_generated_async_path_rejects_dot_segments_before_sending(value):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        async with AsyncBeakr(
            api_key="bk_test_fixture", base_url="https://api.test", http_client=transport
        ) as client:
            try:
                await client.api.projects.get_project(value)
            except ValueError:
                pass
    assert not requests, f"Invalid project id sent a request to {requests[0].url}"
