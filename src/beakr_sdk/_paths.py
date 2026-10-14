"""Path segments for request URLs.

Every value interpolated into a request path goes through :func:`segment`, so an
id can never change which route a request reaches.
"""

from __future__ import annotations

from urllib.parse import quote

__all__ = ["segment"]


def segment(value: object) -> str:
    """One URL path segment, percent-encoded so a value can never add a segment.

    ``.`` and ``..`` survive percent-encoding, and HTTP clients collapse them:
    ``/v1/projects/..`` is sent as ``/v1``, so ``get_project("..")`` would reach
    another route. An empty value would too (``/v1/projects/``). All three are
    refused with ``ValueError`` before anything is sent, never rewritten into a
    different request (audit C057).
    """
    text = str(value)
    if text in {"", ".", ".."}:
        raise ValueError(f"{text!r} is not a valid path value")
    return quote(text, safe="")
