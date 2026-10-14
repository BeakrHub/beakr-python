"""Parse an HTTP-date (RFC 9110 section 5.6.7).

A ``Retry-After`` header may carry an HTTP-date instead of a number of seconds
(RFC 9110 section 10.2.3). Senders must use IMF-fixdate, but recipients must
also accept the two obsolete forms, RFC 850 and ANSI C ``asctime()``::

    Sun, 06 Nov 1994 08:49:37 GMT    ; IMF-fixdate
    Sunday, 06-Nov-94 08:49:37 GMT   ; obsolete RFC 850 format
    Sun Nov  6 08:49:37 1994         ; ANSI C's asctime() format

The grammar is matched here rather than with ``email.utils``, which accepts
many other mail date shapes and reads a two-digit year by a fixed pivot instead
of RFC 9110's "more than 50 years in the future" rule. The TypeScript SDK
(``beakr-typescript/src/http-date.ts``) implements the same grammar so both SDKs
agree on every header.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)"
_DAY_LONG = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MONTH = r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
_TIME = r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"

_IMF_FIXDATE = re.compile(
    rf"{_DAY}, (?P<day>\d{{2}}) {_MONTH} (?P<year>\d{{4}}) {_TIME} GMT", re.IGNORECASE
)
_RFC850 = re.compile(
    rf"{_DAY_LONG}, (?P<day>\d{{2}})-{_MONTH}-(?P<year>\d{{2}}) {_TIME} GMT", re.IGNORECASE
)
_ASCTIME = re.compile(
    rf"{_DAY} {_MONTH} (?P<day>\d{{2}}| \d) {_TIME} (?P<year>\d{{4}})", re.IGNORECASE
)


def parse_http_date(value: str, *, now: datetime) -> datetime | None:
    """The UTC instant an HTTP-date names, or ``None`` when it is not one.

    ``now`` resolves the RFC 850 two-digit year: a year that would land more
    than 50 years after ``now`` is the most recent past year with those digits.
    """
    text = value.strip()
    for pattern in (_IMF_FIXDATE, _RFC850, _ASCTIME):
        match = pattern.fullmatch(text)
        if match is None:
            continue
        year = int(match["year"])
        if pattern is _RFC850:
            year = _expand_two_digit_year(year, now)
        # RFC 9110 allows second 60 for a leap second; datetime does not.
        second = int(match["second"])
        second = 59 if second == 60 else second
        try:
            return datetime(
                year,
                _MONTHS[match["month"].lower()],
                int(match["day"]),
                int(match["hour"]),
                int(match["minute"]),
                second,
                tzinfo=timezone.utc,
            )
        except ValueError:
            # Out-of-range fields (Feb 30, hour 25) are not a date.
            return None
    return None


def _expand_two_digit_year(two_digits: int, now: datetime) -> int:
    year = now.year - now.year % 100 + two_digits
    if year > now.year + 50:
        year -= 100
    return year
