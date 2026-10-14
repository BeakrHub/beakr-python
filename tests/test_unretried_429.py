"""A 429 that no quick retry can fix is returned at once.

Root cause: every 429 was retried (twice, with backoff), on the reading that
the server refused before doing work. An organization out of credits
(``credits_exhausted``) or over a usage quota (``quota_exceeded``) gets the
same refusal on every resend, so the caller waited for nothing and the engine
saw three requests. Other 429s (``rate_limited``) are still retried.
"""

from __future__ import annotations

import pytest
import respx

from beakr_sdk import Beakr
from beakr_sdk.errors import BeakrError

BASE = "https://api.test"


def _refusal(code: str) -> dict:
    return {"error": {"code": code, "message": f"refused: {code}", "fix": "Add credits."}}


@pytest.mark.parametrize("code", ["credits_exhausted", "quota_exceeded"])
@respx.mock
def test_a_429_that_waiting_cannot_fix_is_not_retried(code) -> None:
    route = respx.post(f"{BASE}/v1/knowledge/research").respond(429, json=_refusal(code))
    with Beakr(api_key="bk_live_x", base_url=BASE) as beakr, pytest.raises(BeakrError) as raised:
        beakr.knowledge.research("What changed?")
    assert route.call_count == 1
    assert raised.value.code == code


@respx.mock
def test_a_rate_limit_is_still_retried() -> None:
    route = respx.post(f"{BASE}/v1/knowledge/research").mock(
        side_effect=[
            respx.MockResponse(429, json=_refusal("rate_limited"), headers={"retry-after": "0"}),
            respx.MockResponse(200, json={"text": "ok"}),
        ]
    )
    with Beakr(api_key="bk_live_x", base_url=BASE) as beakr:
        assert beakr.knowledge.research("What changed?") == {"text": "ok"}
    assert route.call_count == 2
