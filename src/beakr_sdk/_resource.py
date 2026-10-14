"""Base class for resources: describe calls, let the client execute them.

A resource method returns whatever the client's executor returns -- a value on
:class:`~beakr_sdk.Beakr`, an awaitable on :class:`~beakr_sdk.AsyncBeakr` -- so
each method is written once for both clients.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from beakr_sdk._calls import Call, Flow, with_params
from beakr_sdk._pagination import page_parts
from beakr_sdk._typing import Async, Sync

if TYPE_CHECKING:
    from beakr_sdk._base_client import BaseClient


#: The largest page a list route serves.
MAX_PAGE_SIZE = 200


#: Which client a resource belongs to (``resources._typing``): ``Sync`` or ``Async``.
M = TypeVar("M", Sync, Async)


class Resource(Generic[M]):
    """A group of operations, shared by the sync and async clients.

    Generic in the client's mode only for type checkers: ``Projects[Sync]`` on
    :class:`~beakr_sdk.Beakr`, ``Projects[Async]`` on ``AsyncBeakr``.
    """

    def __init__(self, client: BaseClient) -> None:
        self._client = client

    def _credential_client(self, client_name: str | None) -> str:
        """The client a new key records: ``client_name``, else this SDK's own name.

        The API requires every key to record a client and refuses a blank one.
        The default is the name this client sends as ``X-Beakr-Client``
        without its version (``sdk-python/1.2.0`` records ``sdk-python``).
        """
        if client_name is None:
            client_name = str(getattr(self._client, "client_name", "") or "sdk-python")
            client_name = client_name.split("/", 1)[0]
        client_name = client_name.strip()
        if not client_name:
            raise ValueError("client_name must name the client the key is for")
        return client_name

    def _run(self, call: Call) -> Any:
        return self._client._execute(call)  # type: ignore[attr-defined]

    def _drive(self, flow: Flow) -> Any:
        return self._client._drive(flow)  # type: ignore[attr-defined]

    def _run_then(self, call: Call, transform: Callable[[Any], Any]) -> Any:
        """Execute ``call`` and return ``transform(result)``, for both clients."""

        def flow() -> Flow:
            result = yield call
            return transform(result)

        return self._drive(flow())

    def _iter(self, call: Call, **kwargs: Any) -> Any:
        return self._client._paginate(call, **kwargs)  # type: ignore[attr-defined]

    def _list_all(self, call: Call, *, page_size: int | None = MAX_PAGE_SIZE) -> Any:
        """Every item of a cursor-paginated list, following ``next_cursor`` to the end.

        Returns the items as one list (awaitable on the async client). Every
        public list answers ``{data, next_cursor, has_more}``; ``page_size``
        sets ``limit`` unless the call already gives one.
        """
        return self._drive(collect_pages(call, page_size=page_size))

    def _stream(self, call: Call) -> Any:
        return self._client._stream_sse(call)  # type: ignore[attr-defined]

    def _follow(self, call: Call, *, last_event_id: str | None, reconnect: bool) -> Any:
        return self._client._follow_sse(  # type: ignore[attr-defined]
            call, last_event_id=last_event_id, reconnect=reconnect
        )


def collect_pages(call: Call, *, page_size: int | None = MAX_PAGE_SIZE) -> Flow:
    """A flow that sends ``call`` page after page and returns every item.

    Usable inside another flow (``items = yield from collect_pages(call)``), so
    a method that first resolves an organization still pages through the list.
    """
    params = dict(call.params or {})
    if page_size and params.get("limit") is None:
        params["limit"] = page_size
    items: list[dict[str, Any]] = []
    while True:
        page = yield with_params(call, params)
        got, next_cursor, has_more = page_parts(page, "data")
        items.extend(got)
        if not (has_more and next_cursor):
            return items
        params["cursor"] = next_cursor


def as_list(result: Any, *keys: str) -> list[dict[str, Any]]:
    """A list from a bare-list body or one wrapped under the first present key."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []
