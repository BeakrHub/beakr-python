"""Research waits as long as the engine allows it, whatever the client's timeout.

Root cause: the engine gives ``POST /v1/knowledge/research`` up to 150s (an
agentic loop that reads documents from connected services), but every call used
the client's timeout, 60s by default, so a slow research answer was abandoned
as a timeout while the engine was still producing it. Research now waits at
least ``RESEARCH_TIMEOUT`` (170s); other calls keep the client's timeout.
"""

from __future__ import annotations

import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr
from beakr_sdk.resources.knowledge import RESEARCH_TIMEOUT

BASE = "https://api.test"


def _read_timeout(route: respx.Route) -> float:
    return route.calls.last.request.extensions["timeout"]["read"]


@respx.mock
def test_research_outwaits_the_engines_route_timeout() -> None:
    research = respx.post(f"{BASE}/v1/knowledge/research").respond(200, json={"text": "ok"})
    projects = respx.get(f"{BASE}/v1/projects").respond(
        200, json={"data": [], "next_cursor": None, "has_more": False}
    )
    with Beakr(api_key="bk_live_x", base_url=BASE) as beakr:
        beakr.knowledge.research("What changed?")
        beakr.projects.list()
    assert RESEARCH_TIMEOUT > 150
    assert _read_timeout(research) == RESEARCH_TIMEOUT
    assert _read_timeout(projects) == 60.0


@respx.mock
def test_a_longer_client_timeout_still_wins() -> None:
    research = respx.post(f"{BASE}/v1/knowledge/research").respond(200, json={"text": "ok"})
    with Beakr(api_key="bk_live_x", base_url=BASE, timeout=300.0) as beakr:
        beakr.knowledge.research("What changed?")
    assert _read_timeout(research) == 300.0


@pytest.mark.asyncio
@respx.mock
async def test_the_async_client_waits_as_long() -> None:
    research = respx.post(f"{BASE}/v1/knowledge/research").respond(200, json={"text": "ok"})
    async with AsyncBeakr(api_key="bk_live_x", base_url=BASE) as beakr:
        await beakr.knowledge.research("What changed?")
    assert _read_timeout(research) == RESEARCH_TIMEOUT
