"""The asynchronous Beakr client.

Example::

    import asyncio
    from beakr_sdk import AsyncBeakr

    async def main() -> None:
        async with AsyncBeakr() as beakr:
            context = await beakr.context(format="markdown")
            print(context["content"])
            async for event in beakr.events.iter(follow=False):
                print(event["type"])

    asyncio.run(main())

Every resource method has the same name and arguments as on :class:`Beakr`;
single calls return awaitables and iterators are async iterators.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx

from beakr_sdk._base_client import BaseClient
from beakr_sdk._calls import (
    _UNSET,
    Call,
    Flow,
    RequestResponse,
    Sleep,
    as_stream,
    operation_call,
    with_params,
)
from beakr_sdk._generated.operations import AsyncBeakrApi
from beakr_sdk._pagination import list_call_params, page_parts, resume_cursor
from beakr_sdk._sse import STREAM_COMPLETE, SSEParser
from beakr_sdk._typing import Async
from beakr_sdk.credentials import RefreshLock
from beakr_sdk.errors import APIConnectionError, BeakrError, error_from_response
from beakr_sdk.resources import attach_resources

if TYPE_CHECKING:
    from beakr_sdk.resources.bulk_uploads import BulkUploads
    from beakr_sdk.resources.conversations import Conversations
    from beakr_sdk.resources.end_user_tokens import EndUserTokens
    from beakr_sdk.resources.engine import Engine
    from beakr_sdk.resources.external_users import ExternalUsers
    from beakr_sdk.resources.files import Files
    from beakr_sdk.resources.folders import Folders
    from beakr_sdk.resources.integrations import Integrations
    from beakr_sdk.resources.interviews import Interviews
    from beakr_sdk.resources.jobs import Jobs
    from beakr_sdk.resources.keys import Keys
    from beakr_sdk.resources.knowledge import Knowledge
    from beakr_sdk.resources.labels import Labels
    from beakr_sdk.resources.me import Me, Meta
    from beakr_sdk.resources.members import Members
    from beakr_sdk.resources.org_admin import OrgAdmin
    from beakr_sdk.resources.organizations import Organizations
    from beakr_sdk.resources.projects import Projects
    from beakr_sdk.resources.runs import Runs, Threads


class _LockAcquisition:
    """Take a ``RefreshLock`` on a worker thread without stranding it on cancel.

    ``asyncio.to_thread`` keeps running after its awaiter is cancelled, so a lock
    the thread acquires afterwards would otherwise never be released and would
    block every other process sharing the login (audit C055). On cancellation
    the awaiter keeps waiting (shielded, bounded by the lock's own timeout) for
    the thread to finish and releases what it got before re-raising; if it is
    cancelled again meanwhile, the thread releases the lock itself the moment
    it acquires it.
    """

    def __init__(self, lock: RefreshLock) -> None:
        self._lock = lock
        self._mutex = threading.Lock()
        self._abandoned = False
        self._held = False

    def _acquire(self) -> None:
        self._lock.acquire()
        with self._mutex:
            if self._abandoned:
                self._lock.release()
                return
            self._held = True

    async def wait(self) -> None:
        thread = asyncio.ensure_future(asyncio.to_thread(self._acquire))
        try:
            await asyncio.shield(thread)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(thread)
            except BaseException:  # noqa: BLE001 - the original cancellation is re-raised below
                pass
            finally:
                self.abandon()
            raise

    def abandon(self) -> None:
        """Release the lock if held; a still-pending acquisition releases on arrival."""
        with self._mutex:
            self._abandoned = True
            if self._held:
                self._held = False
                self._lock.release()


class AsyncBeakr(BaseClient):
    """Asynchronous client; same arguments as :class:`beakr_sdk.Beakr`."""

    api: AsyncBeakrApi
    # Attached by ``resources.attach_resources``; typed for this client's mode.
    keys: Keys[Async]
    me: Me[Async]
    meta: Meta[Async]
    jobs: Jobs[Async]
    organizations: Organizations[Async]
    members: Members[Async]
    org: OrgAdmin[Async]
    external_users: ExternalUsers[Async]
    labels: Labels[Async]
    end_user_tokens: EndUserTokens[Async]
    projects: Projects[Async]
    files: Files[Async]
    bulk_uploads: BulkUploads[Async]
    folders: Folders[Async]
    knowledge: Knowledge[Async]
    runs: Runs[Async]
    interviews: Interviews[Async]
    threads: Threads[Async]
    conversations: Conversations[Async]
    engine: Engine[Async]
    integrations: Integrations[Async]
    # Shortcuts attached beside them (``engine.context``, ``engine.compound``,
    # ``jobs.wait``).
    context: Callable[..., Any]
    compound: Callable[..., Any]
    wait: Callable[..., Any]

    def __init__(self, *, http_client: httpx.AsyncClient | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout)
        self._owns_http = http_client is None
        self._refresh_lock = asyncio.Lock()
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        attach_resources(self)
        #: Every public operation, one typed method each (generated from the contract).
        self.api = AsyncBeakrApi(self)

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def paginate(
        self, path: str, *, page_size: int | None = None, **params: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Every item of any public list, following ``next_cursor``.

        ``async for project in beakr.paginate("/v1/projects"): ...`` -- see
        :meth:`beakr_sdk.Beakr.paginate`.
        """
        return self._paginate(Call("GET", path, params=list_call_params(page_size, params)))

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = _UNSET,
        fields: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        response: RequestResponse = "json",
        authenticated: bool = True,
    ) -> Any:
        """Call any public operation; see :meth:`beakr_sdk.Beakr.request`.

        With ``response="sse"`` this returns an async iterator of events.
        """
        call = operation_call(
            method,
            path,
            params=params,
            json=json,
            fields=fields,
            files=files,
            response=response,
            authenticated=authenticated,
        )
        if response == "sse":
            return self._follow_sse(call, last_event_id=None, reconnect=False)
        return await self._execute(call)

    async def __aenter__(self) -> AsyncBeakr:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- execution ---------------------------------------------------------

    async def _refresh(self, stale_access_token: str | None) -> None:
        async with self._refresh_lock:
            tokens = self.credential.tokens
            if tokens is not None and tokens.access_token != stale_access_token:
                return
            if not self._uses_stored_login():
                await self._refresh_now()
                return
            acquisition = _LockAcquisition(RefreshLock())
            await acquisition.wait()
            try:
                if self._adopt_rotated_pair() and not self._still_needs_refresh(stale_access_token):
                    return
                await self._refresh_now()
            finally:
                acquisition.abandon()

    async def _refresh_now(self) -> None:
        self._assert_refreshable()
        try:
            data = await self._execute(self._refresh_call())
        except BeakrError as exc:
            self._settle_failed_refresh(exc)
        self._apply_refreshed_tokens(data)

    def _current_access_token(self) -> str | None:
        tokens = self.credential.tokens
        return tokens.access_token if tokens else None

    async def _send(self, call: Call) -> httpx.Response:
        idempotency_key = self._new_idempotency_key(call)
        attempt = 0
        refreshed = False
        while True:
            if call.authenticated and not call.external and self._needs_proactive_refresh():
                await self._refresh(self._current_access_token())
                refreshed = True
            request = self._build_request(call, idempotency_key)
            try:
                response = await self._http.send(request, stream=call.response == "stream")
            except httpx.TransportError as exc:
                delay = self._retry_delay(call, attempt, None)
                if delay is None:
                    raise APIConnectionError(f"Could not reach {request.url.host}: {exc}") from exc
                await self._sleep(delay)
                attempt += 1
                continue
            if response.is_success:
                return response
            if call.response == "stream":
                await response.aread()
                await response.aclose()
            if self._auth_retry_allowed(call, response, refreshed):
                await self._refresh(self._current_access_token())
                refreshed = True
                continue
            delay = self._retry_delay(call, attempt, response)
            if delay is None:
                raise error_from_response(response, now=self._now())
            await self._sleep(delay)
            attempt += 1

    async def _execute(self, call: Call) -> Any:
        return self._decode(call, await self._send(call))

    async def _drive(self, flow: Flow) -> Any:
        try:
            step = next(flow)
            while True:
                try:
                    if isinstance(step, Sleep):
                        await self._sleep(step.seconds)
                        result: Any = None
                    else:
                        result = await self._execute(step)
                except BeakrError as exc:
                    step = flow.throw(exc)
                    continue
                step = flow.send(result)
        except StopIteration as stop:
            return stop.value

    async def _paginate(
        self,
        call: Call,
        *,
        items_key: str = "data",
        follow: bool = False,
        poll_interval: float = 5.0,
    ) -> AsyncIterator[dict[str, Any]]:
        params = dict(call.params or {})
        while True:
            page = await self._execute(with_params(call, params))
            items, next_cursor, has_more = page_parts(page, items_key)
            for item in items:
                yield item
            params["cursor"] = resume_cursor(params.get("cursor"), next_cursor, page)
            if has_more and next_cursor:
                continue
            if not follow:
                return
            await self._sleep(poll_interval)

    async def _follow_sse(
        self, call: Call, *, last_event_id: str | None, reconnect: bool
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield events; each time the server ends the stream, resume after its last id."""
        while True:
            headers = dict(call.headers)
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            parser = SSEParser()
            response = await self._send(as_stream(replace(call, headers=headers)))
            try:
                async for line in response.aiter_lines():
                    event = parser.feed(line)
                    if event is not None:
                        yield event
            finally:
                await response.aclose()
            last_event_id = parser.last_event_id or last_event_id
            if not reconnect:
                return

    async def _stream_sse(self, call: Call) -> AsyncIterator[dict[str, Any]]:
        response = await self._send(as_stream(call))
        parser = SSEParser()
        try:
            async for line in response.aiter_lines():
                event = parser.feed(line)
                if event is None:
                    continue
                yield event
                if event.get("type") == STREAM_COMPLETE:
                    return
        finally:
            await response.aclose()
