"""Retry-After's HTTP-date form must not turn a server delay into jitter."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import respx

from beakr_sdk import RateLimitError

BASE = "https://api.test"


def date_after(seconds):
    return format_datetime(datetime.now(timezone.utc) + timedelta(seconds=seconds), usegmt=True)


@pytest.mark.parametrize("status", [429, 503])
@respx.mock
def test_sync_client_obeys_http_date(client, sleeps, status):
    respx.get(f"{BASE}/v1/me").mock(
        side_effect=[
            httpx.Response(status, headers={"Retry-After": date_after(30)}),
            httpx.Response(200, json={"id": "me"}),
        ]
    )
    assert client.me.get() == {"id": "me"}
    assert len(sleeps) == 1 and 25 <= sleeps[0] <= 30, sleeps


@pytest.mark.parametrize("status", [429, 503])
@respx.mock
async def test_async_client_obeys_http_date(async_client, sleeps, status):
    respx.get(f"{BASE}/v1/me").mock(
        side_effect=[
            httpx.Response(status, headers={"Retry-After": date_after(30)}),
            httpx.Response(200, json={"id": "me"}),
        ]
    )
    assert await async_client.me.get() == {"id": "me"}
    assert len(sleeps) == 1 and 25 <= sleeps[0] <= 30, sleeps


@respx.mock
def test_long_date_surfaces_rate_limit_without_an_early_retry(client, sleeps):
    route = respx.get(f"{BASE}/v1/me").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": date_after(3600)}),
            httpx.Response(200, json={"id": "me"}),
        ]
    )
    with pytest.raises(RateLimitError) as caught:
        client.me.get()
    assert len(route.calls) == 1 and not sleeps
    assert 3590 <= caught.value.retry_after <= 3600
