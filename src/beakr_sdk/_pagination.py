"""Reading the cursor-paginated list shape ``{data, next_cursor, has_more}``."""

from __future__ import annotations

from typing import Any


def page_parts(page: Any, items_key: str) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Items, next cursor and has_more. A bare list (older routes) is one whole page."""
    if isinstance(page, list):
        return page, None, False
    if not isinstance(page, dict):
        return [], None, False
    items = page.get(items_key) or []
    next_cursor = page.get("next_cursor")
    has_more = bool(page.get("has_more", next_cursor is not None))
    return list(items), next_cursor, has_more


def resume_cursor(current: str | None, next_cursor: str | None, page: Any) -> str | None:
    """Where the next request starts: ``next_cursor``, else the page's ``cursor``.

    A final page omits ``next_cursor``. The event stream then states where a
    follower resumes in a top-level ``cursor`` (the engine's encoded event
    sequence). Never synthesize a cursor from an item's id: the engine's cursors
    are opaque encodings, so an item id sent back is an invalid cursor and a
    follower stalls on errors after catching up.
    """
    if next_cursor:
        return next_cursor
    if isinstance(page, dict) and page.get("cursor"):
        return str(page["cursor"])
    return current


def list_call_params(page_size: int | None, params: dict[str, Any]) -> dict[str, Any]:
    """Query parameters for a list request: the filters given, plus ``limit``."""
    query = {key: value for key, value in params.items() if value is not None}
    if page_size is not None:
        query["limit"] = page_size
    return query
