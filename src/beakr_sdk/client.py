"""The synchronous Beakr client.

Example::

    from beakr_sdk import Beakr

    beakr = Beakr()  # BEAKR_API_KEY, or the CLI login
    for project in beakr.projects.list():
        print(project["name"])
    card = beakr.knowledge.cat("Billing", scope="Platform")
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
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
from beakr_sdk._generated.operations import BeakrApi
from beakr_sdk._pagination import list_call_params, page_parts, resume_cursor
from beakr_sdk._sse import STREAM_COMPLETE, SSEParser
from beakr_sdk._typing import Sync
from beakr_sdk.credentials import Credential, refresh_lock
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
    from beakr_sdk.resources.skills import Skills


class Beakr(BaseClient):
    """Synchronous client. Thread-safe for concurrent requests; close it when done.

    Args:
        api_key: A ``bk_live_`` API key or ``bk_at_`` access token. Defaults to
            the chain in :mod:`beakr_sdk.credentials`.
        base_url: API base URL. Defaults to ``BEAKR_BASE_URL`` / ``BEAKR_API_URL``,
            then the CLI config, then ``https://api.thebeakr.com``.
        client_name: Sent as ``X-Beakr-Client``; defaults to ``sdk-python/<version>``.
        agent: Sent as ``X-Beakr-Agent`` to attribute writes, e.g. ``"my-bot"``.
        run: Sent as ``X-Beakr-Run``: groups this client's Knowledge writes into one
            run that ``knowledge.undo_run(run)`` can take back together.
        timeout: Per-request timeout in seconds.
        max_retries: Retries on 429/502/503/504 and connection errors.
    """

    api: BeakrApi
    # Attached by ``resources.attach_resources``; typed for this client's mode.
    keys: Keys[Sync]
    me: Me[Sync]
    meta: Meta[Sync]
    jobs: Jobs[Sync]
    organizations: Organizations[Sync]
    members: Members[Sync]
    org: OrgAdmin[Sync]
    external_users: ExternalUsers[Sync]
    labels: Labels[Sync]
    end_user_tokens: EndUserTokens[Sync]
    projects: Projects[Sync]
    files: Files[Sync]
    bulk_uploads: BulkUploads[Sync]
    folders: Folders[Sync]
    knowledge: Knowledge[Sync]
    runs: Runs[Sync]
    interviews: Interviews[Sync]
    skills: Skills[Sync]
    threads: Threads[Sync]
    conversations: Conversations[Sync]
    engine: Engine[Sync]
    integrations: Integrations[Sync]
    # Shortcuts attached beside them (``engine.context``, ``engine.compound``,
    # ``jobs.wait``).
    context: Callable[..., Any]
    compound: Callable[..., Any]
    wait: Callable[..., Any]

    def __init__(self, *, http_client: httpx.Client | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._http = http_client or httpx.Client(timeout=self.timeout)
        self._owns_http = http_client is None
        self._refresh_lock = threading.Lock()
        #: Injectable so tests do not actually wait out backoff.
        self._sleep: Callable[[float], None] = time.sleep
        attach_resources(self)
        #: Every public operation, one typed method each (generated from the contract).
        self.api = BeakrApi(self)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def paginate(
        self, path: str, *, page_size: int | None = None, **params: Any
    ) -> Iterator[dict[str, Any]]:
        """Every item of any public list, following ``next_cursor``.

        Every list route answers ``{data, next_cursor, has_more}``, so this one
        iterator walks any of them: ``for project in beakr.paginate("/v1/projects")``.
        ``params`` are the list's filters; ``page_size`` is its ``limit``.
        """
        return self._paginate(Call("GET", path, params=list_call_params(page_size, params)))

    def request(
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
        """Call any public operation by method and path, with this client's auth.

        The typed resources (``beakr.projects.create``...) are the usual way in;
        this reaches every operation in the public contract the same way --
        credentials, refresh, retries, ``Idempotency-Key`` and typed errors --
        and is what ``beakr api`` uses. ``path`` is concrete
        (``/v1/projects/<id>``; build segments with ``beakr_sdk._paths.segment``).
        ``response="sse"`` returns an iterator of events; ``"bytes"`` a file's
        content. ``authenticated=False`` sends no credential, for the
        operations the contract marks anonymous.
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
        return self._execute(call)

    def __enter__(self) -> Beakr:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def ensure_fresh_credential(self, *, rejected_access_token: str | None = None) -> Credential:
        """Resolve the credential, refreshing a stored access token that is about to expire.

        For callers that build their own HTTP requests from
        ``credential.auth_headers()`` (the CLI's legacy commands, its MCP
        server, and ``beakr auth token`` for other-language SDKs) and so never
        pass through the refresh-on-401 path.

        ``rejected_access_token`` is the caller's own refresh-on-401: the API
        refused that access token, so refresh even though its clock has not
        run out -- but only while it is still the current one. Under the
        refresh lock, a pair another process already rotated is adopted
        instead of spending the refresh token again.
        """
        current = self._current_access_token()
        if rejected_access_token is not None and current == rejected_access_token:
            self._refresh(current)
        elif self._needs_proactive_refresh():
            self._refresh(self._current_access_token())
        return self.credential

    # -- execution ---------------------------------------------------------

    def _refresh(self, stale_access_token: str | None) -> None:
        with self._refresh_lock:
            tokens = self.credential.tokens
            # Another thread refreshed while this one waited for the lock.
            if tokens is not None and tokens.access_token != stale_access_token:
                return
            if not self._uses_stored_login():
                self._refresh_now()
                return
            with refresh_lock():
                # Another process may have rotated the stored pair already.
                if self._adopt_rotated_pair() and not self._still_needs_refresh(stale_access_token):
                    return
                self._refresh_now()

    def _refresh_now(self) -> None:
        self._assert_refreshable()
        try:
            data = self._execute(self._refresh_call())
        except BeakrError as exc:
            self._settle_failed_refresh(exc)
        self._apply_refreshed_tokens(data)

    def _current_access_token(self) -> str | None:
        tokens = self.credential.tokens
        return tokens.access_token if tokens else None

    def _send(self, call: Call) -> httpx.Response:
        """Send with retries and one refresh-on-401; return the final success response."""
        idempotency_key = self._new_idempotency_key(call)
        attempt = 0
        refreshed = False
        while True:
            if call.authenticated and not call.external and self._needs_proactive_refresh():
                self._refresh(self._current_access_token())
                refreshed = True
            request = self._build_request(call, idempotency_key)
            try:
                response = self._http.send(request, stream=call.response == "stream")
            except httpx.TransportError as exc:
                delay = self._retry_delay(call, attempt, None)
                if delay is None:
                    raise APIConnectionError(f"Could not reach {request.url.host}: {exc}") from exc
                self._sleep(delay)
                attempt += 1
                continue
            if response.is_success:
                return response
            if call.response == "stream":
                response.read()
                response.close()
            if self._auth_retry_allowed(call, response, refreshed):
                self._refresh(self._current_access_token())
                refreshed = True
                continue
            delay = self._retry_delay(call, attempt, response)
            if delay is None:
                raise error_from_response(response, now=self._now())
            self._sleep(delay)
            attempt += 1

    def _execute(self, call: Call) -> Any:
        return self._decode(call, self._send(call))

    def _drive(self, flow: Flow) -> Any:
        """Run a flow to completion, feeding each step's result (or error) back in."""
        try:
            step = next(flow)
            while True:
                try:
                    if isinstance(step, Sleep):
                        self._sleep(step.seconds)
                        result: Any = None
                    else:
                        result = self._execute(step)
                except BeakrError as exc:
                    step = flow.throw(exc)
                    continue
                step = flow.send(result)
        except StopIteration as stop:
            return stop.value

    def _paginate(
        self,
        call: Call,
        *,
        items_key: str = "data",
        follow: bool = False,
        poll_interval: float = 5.0,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item, following ``next_cursor``; with ``follow``, poll forever."""
        params = dict(call.params or {})
        while True:
            page = self._execute(with_params(call, params))
            items, next_cursor, has_more = page_parts(page, items_key)
            yield from items
            params["cursor"] = resume_cursor(params.get("cursor"), next_cursor, page)
            if has_more and next_cursor:
                continue
            if not follow:
                return
            self._sleep(poll_interval)

    def _follow_sse(
        self, call: Call, *, last_event_id: str | None, reconnect: bool
    ) -> Iterator[dict[str, Any]]:
        """Yield events; each time the server ends the stream, resume after its last id."""
        while True:
            headers = dict(call.headers)
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            parser = SSEParser()
            response = self._send(as_stream(replace(call, headers=headers)))
            try:
                for line in response.iter_lines():
                    event = parser.feed(line)
                    if event is not None:
                        yield event
            finally:
                response.close()
            last_event_id = parser.last_event_id or last_event_id
            if not reconnect:
                return

    def _stream_sse(self, call: Call) -> Iterator[dict[str, Any]]:
        """Yield parsed events until ``stream_complete`` or the server closes."""
        stream_call = as_stream(call)
        response = self._send(stream_call)
        parser = SSEParser()
        try:
            for line in response.iter_lines():
                event = parser.feed(line)
                if event is None:
                    continue
                yield event
                if event.get("type") == STREAM_COMPLETE:
                    return
        finally:
            response.close()
