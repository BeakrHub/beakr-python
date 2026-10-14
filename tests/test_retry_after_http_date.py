"""Retry-After as an HTTP-date (RFC 9110 section 10.2.3), on a pinned clock.

Root cause these guard: ``parse_retry_after`` accepted only ``float(value)``,
so a date-form Retry-After was treated as absent and the SDK retried after
normal jitter -- early for a near deadline, and immediately for one past the
60-second cap that should surface the error instead.
"""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from beakr_sdk import RateLimitError
from beakr_sdk.errors import parse_retry_after

BASE = "https://api.test"
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)

#: 30 seconds after NOW in each of the three forms recipients must accept.
IMF_FIXDATE = "Sun, 11 Oct 2026 12:00:30 GMT"
RFC850 = "Sunday, 11-Oct-26 12:00:30 GMT"
ASCTIME = "Sun Oct 11 12:00:30 2026"


@pytest.fixture
def pinned(client, async_client):
    client._now = lambda: NOW
    async_client._now = lambda: NOW
    return client, async_client


# -- parse_retry_after -------------------------------------------------------


@pytest.mark.parametrize("value", [IMF_FIXDATE, RFC850, ASCTIME])
def test_every_http_date_form_becomes_seconds_from_now(value):
    assert parse_retry_after(value, now=NOW) == 30.0


def test_asctime_single_digit_day_is_space_padded():
    assert parse_retry_after(
        "Sat Oct  3 12:00:00 2026", now=datetime(2026, 10, 3, tzinfo=timezone.utc)
    ) == (12 * 3600.0)


def test_month_and_day_names_are_case_insensitive():
    assert parse_retry_after("sun, 11 OCT 2026 12:00:30 gmt", now=NOW) == 30.0


@pytest.mark.parametrize(
    ("two_digits", "year"),
    [("26", 2026), ("76", 2076), ("77", 1977), ("94", 1994)],
)
def test_rfc850_two_digit_year_more_than_50_years_ahead_is_the_past_century(two_digits, year):
    deadline = parse_retry_after(f"Monday, 01-Jan-{two_digits} 00:00:00 GMT", now=NOW)
    expected = (datetime(year, 1, 1, tzinfo=timezone.utc) - NOW).total_seconds()
    assert deadline == max(0.0, expected)


def test_past_date_is_zero_not_negative():
    assert parse_retry_after("Sun, 06 Nov 1994 08:49:37 GMT", now=NOW) == 0.0
    assert parse_retry_after("Sun, 11 Oct 2026 11:59:59 GMT", now=NOW) == 0.0


@pytest.mark.parametrize(
    "value",
    [
        "soon",
        "Sun, 31 Feb 2026 12:00:00 GMT",  # no such day
        "Sun, 11 Oct 2026 25:00:00 GMT",  # no such hour
        "Sun, 11 Oct 2026 12:00:30 PST",  # HTTP-dates are always GMT
        "Sun, 11 Foo 2026 12:00:30 GMT",
        "2026-10-11T12:00:30Z",  # ISO 8601 is not an HTTP-date
        "Sun, 11 Oct 2026",
    ],
)
def test_malformed_date_is_absent(value):
    assert parse_retry_after(value, now=NOW) is None


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("30", 30.0), ("1.5", 1.5), ("0", 0.0), ("-5", 0.0), ("3600", 3600.0)],
)
def test_numeric_delay_seconds_unchanged(value, seconds):
    assert parse_retry_after(value, now=NOW) == seconds


@pytest.mark.parametrize("value", [None, ""])
def test_missing_header_is_absent(value):
    assert parse_retry_after(value, now=NOW) is None


def test_default_clock_is_the_current_time():
    assert parse_retry_after("Sun, 06 Nov 1994 08:49:37 GMT") == 0.0


# -- sync and async clients --------------------------------------------------


def _mock_twice(status, header):
    return respx.get(f"{BASE}/v1/me").mock(
        side_effect=[
            httpx.Response(status, headers={"Retry-After": header}),
            httpx.Response(200, json={"id": "me"}),
        ]
    )


@pytest.mark.parametrize(
    ("status", "header", "expected"),
    [
        (429, IMF_FIXDATE, 30.0),
        (503, RFC850, 30.0),
        (429, ASCTIME, 30.0),
        (429, "Sun, 06 Nov 1994 08:49:37 GMT", 0.0),  # past: retry now
        (429, "Sun, 11 Oct 2026 12:01:00 GMT", 60.0),  # exactly the cap: waited out
        (503, "12", 12.0),  # numeric unchanged
        (429, "60", 60.0),
    ],
)
@respx.mock
def test_sync_client_waits_the_parsed_deadline(pinned, sleeps, status, header, expected):
    client, _ = pinned
    route = _mock_twice(status, header)
    assert client.me.get() == {"id": "me"}
    assert len(route.calls) == 2
    assert sleeps == [expected]


@pytest.mark.parametrize(
    ("status", "header", "expected"),
    [
        (429, IMF_FIXDATE, 30.0),
        (503, RFC850, 30.0),
        (429, ASCTIME, 30.0),
        (429, "Sun, 06 Nov 1994 08:49:37 GMT", 0.0),
        (429, "Sun, 11 Oct 2026 12:01:00 GMT", 60.0),
        (503, "12", 12.0),
        (429, "60", 60.0),
    ],
)
@respx.mock
async def test_async_client_waits_the_parsed_deadline(pinned, sleeps, status, header, expected):
    _, async_client = pinned
    route = _mock_twice(status, header)
    assert await async_client.me.get() == {"id": "me"}
    assert len(route.calls) == 2
    assert sleeps == [expected]


@pytest.mark.parametrize("header", ["Sun, 11 Oct 2026 12:01:01 GMT", "61"])
@respx.mock
def test_sync_deadline_past_the_cap_surfaces_the_error_without_retrying(pinned, sleeps, header):
    client, _ = pinned
    route = _mock_twice(429, header)
    with pytest.raises(RateLimitError) as caught:
        client.me.get()
    assert len(route.calls) == 1 and sleeps == []
    assert caught.value.retry_after == 61.0


@pytest.mark.parametrize("header", ["Sun, 11 Oct 2026 12:01:01 GMT", "61"])
@respx.mock
async def test_async_deadline_past_the_cap_surfaces_the_error_without_retrying(
    pinned, sleeps, header
):
    _, async_client = pinned
    route = _mock_twice(429, header)
    with pytest.raises(RateLimitError) as caught:
        await async_client.me.get()
    assert len(route.calls) == 1 and sleeps == []
    assert caught.value.retry_after == 61.0


@respx.mock
def test_sync_malformed_date_falls_back_to_default_backoff(pinned, sleeps):
    client, _ = pinned
    _mock_twice(429, "Sun, 31 Feb 2026 12:00:00 GMT")
    assert client.me.get() == {"id": "me"}
    # First retry: half-second base with full jitter.
    assert len(sleeps) == 1 and 0.25 <= sleeps[0] <= 0.5, sleeps


@respx.mock
async def test_async_malformed_date_falls_back_to_default_backoff(pinned, sleeps):
    _, async_client = pinned
    _mock_twice(429, "not a date")
    assert await async_client.me.get() == {"id": "me"}
    assert len(sleeps) == 1 and 0.25 <= sleeps[0] <= 0.5, sleeps


@respx.mock
def test_rate_limit_error_reports_date_deadline_in_seconds(pinned, sleeps):
    client, _ = pinned
    client.max_retries = 0
    respx.get(f"{BASE}/v1/me").mock(
        return_value=httpx.Response(429, headers={"Retry-After": IMF_FIXDATE})
    )
    with pytest.raises(RateLimitError) as caught:
        client.me.get()
    assert caught.value.retry_after == 30.0 and sleeps == []
