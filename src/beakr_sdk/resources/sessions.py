"""Sessions: an agent session that reads and writes only the projects a person chooses.

Start a session with your own credential and get back a client confined to it.
Every request that client makes reads only the session's read projects and
writes only its write projects; the person can change them in the web app
(``picker_url``) while it runs, and the change applies to the next request. The
session's token (``bk_st_``) lasts an hour by default; the scoped client mints
another with your credential before it runs out.

Example::

    scoped = beakr.sessions.create(read=[docs_project], write=[notes_project])
    scoped.client.knowledge.search("assay results")   # reads docs and notes
    beakr.sessions.end(scoped.id)

A session started without projects is pending: its requests raise
:class:`~beakr_sdk.errors.SessionNeedsSelectionError` until the person chooses
at ``scoped.picker_url``. A session's own token may only drop projects
(:meth:`Sessions.update` with it raises
:class:`~beakr_sdk.errors.SessionWidenRefusedError` for anything else).

Where the server keeps a session per agent conversation, no token is needed: a
person's API key that sends ``X-Beakr-Session: <id>`` runs inside that person's
session for the id, started on first use. Every client sends one (its
``session``, else ``BEAKR_SESSION``, else an id it makes once), so a script
lands in one session, and project boundaries apply to it as to an agent.
:meth:`Sessions.start` opens a fresh one for a block of work::

    with beakr.sessions.start(label="nightly import") as s:
        s.client.knowledge.write(...)
        print(s.status()["summary"])
    # ended here; the id is never used again

Project boundaries. Each project has a ``boundary`` (``content_out``: ``allowed``
or ``locked``; ``content_in``: ``allowed``, ``owner_approves`` or ``blocked``), and
a session works one of three ways (``mode``):

- ``automatic``: nothing declared; it starts open and the server tracks what it
  touches. Touching a locked project locks its writes to that project
  (``SessionLockedToProjectError`` elsewhere); a second one makes it read-only
  (``SessionReadOnlyError``).
- ``picked``: ``read`` and ``write`` lists declared at the start, checked against
  the boundaries (``SessionSelectionInvalidError`` with every reason);
  :meth:`Sessions.options` says which projects can be added before starting.
- ``read_everything``: every project the person can read, locked ones included,
  and no writes.

A declaration is applied only while the session has touched nothing
(``SessionAlreadyStartedError`` after). :meth:`Sessions.status` says where a
session can still write, and why::

    with beakr.sessions.start(read=[partner_a], write=[partner_a]) as s:
        print(s.session["summary"])   # "Picked session: reads Partner A; writes Partner A."
        print(s.status()["write"])
"""

from __future__ import annotations

import builtins
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from beakr_sdk._base_client import new_session_id
from beakr_sdk._calls import Call, Flow, adjusted, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.credentials import REFRESH_SKEW_SECONDS
from beakr_sdk.errors import NotFoundError, UnexpectedResponseError, ValidationError
from beakr_sdk.resources._base import Resource


def _expires_epoch(value: Any) -> float:
    """Seconds since the epoch for an ISO 8601 ``expires_at``; now when unreadable."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


@dataclass
class SessionRenewal:
    """Keeps a scoped client's session token fresh, minting with the client that started it."""

    parent: Any
    session_id: str
    expires_at: float
    ttl_seconds: int | None = None

    def due(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_SKEW_SECONDS

    def mint_call(self) -> Call:
        body = clean_body({"ttl_seconds": self.ttl_seconds})
        # A retried mint mints another token, so the request is not idempotent.
        return adjusted(
            ops.build_sessions_mint_session_token(self.session_id, body=body),  # type: ignore[arg-type]
            idempotent=False,
        )

    def apply(self, scoped: Any, minted: dict[str, Any]) -> None:
        scoped.credential.token = minted["token"]
        self.expires_at = _expires_epoch(minted.get("expires_at"))

    def renew(self, scoped: Any) -> None:
        self.apply(scoped, self.parent._execute(self.mint_call()))

    async def renew_async(self, scoped: Any) -> None:
        self.apply(scoped, await self.parent._execute(self.mint_call()))


@dataclass
class ScopedSession:
    """A session and a client confined to it.

    ``client`` is a :class:`~beakr_sdk.Beakr` (or ``AsyncBeakr``, matching the
    client that started it) whose bearer is the session's token; it shares that
    client's connection pool, so closing it closes nothing.
    """

    session: dict[str, Any]
    client: Any = field(repr=False)

    @property
    def id(self) -> str:
        return str(self.session["id"])

    @property
    def status(self) -> str:
        return str(self.session.get("status"))

    @property
    def picker_url(self) -> str:
        """Where the person chooses and changes the session's projects."""
        return str(self.session.get("picker_url"))


def scoped_client(parent: Any, session_id: str, token: dict[str, Any], *, ttl: int | None) -> Any:
    """A client of ``parent``'s kind that sends ``token`` and renews it through ``parent``."""
    if not token.get("token"):
        raise UnexpectedResponseError(
            "The API answered without a session token; the session was created but no "
            "scoped client can be built. Mint one with sessions.scoped(session_id)."
        )
    client = type(parent)(
        api_key=token["token"],
        base_url=parent.base_url,
        client_name=parent.client_name,
        agent=parent.agent,
        session=parent.session or False,
        org_id=parent.org_id,
        timeout=parent.timeout,
        max_retries=parent.max_retries,
        default_headers=parent._default_headers,
        http_client=parent._http,
    )
    client._session_renewal = SessionRenewal(
        parent=parent,
        session_id=session_id,
        expires_at=_expires_epoch(token.get("expires_at")),
        ttl_seconds=ttl,
    )
    return client


def session_client(parent: Any, session: str) -> Any:
    """A client like ``parent`` that sends ``session`` as ``X-Beakr-Session``.

    It shares ``parent``'s credential (and its refresh), connection pool and
    settings, so closing it closes nothing.
    """
    client = type(parent)(
        api_key=parent._explicit_api_key,
        base_url=parent.base_url,
        client_name=parent.client_name,
        agent=parent.agent,
        run=parent.run,
        session=session,
        org_id=parent.org_id,
        timeout=parent.timeout,
        max_retries=parent.max_retries,
        default_headers=parent._default_headers,
        token_store=parent._token_store,
        http_client=parent._http,
    )
    client._credential = parent.credential
    client._session_renewal = parent._session_renewal
    client._refresh_lock = parent._refresh_lock
    client._sleep = parent._sleep
    client._now = parent._now
    return client


@dataclass
class AgentSession:
    """A session joined by ``X-Beakr-Session``, and a client that sends its id.

    ``session`` is the server's record of it (``mode``, ``lock_state``,
    ``write_project``, ``touched_projects``...) as it was when it started; it
    is ``None`` on a server without sessions per agent conversation, where the
    id only labels the writes. :meth:`current` reads it again.
    """

    agent_session_id: str
    client: Any = field(repr=False)
    session: dict[str, Any] | None = None

    @property
    def id(self) -> str | None:
        """The session's id on the server (``None`` where the server keeps none)."""
        return str(self.session["id"]) if self.session else None

    def current(self) -> Any:
        """The session now: its lock state, write project and the projects it touched."""
        return self.client.sessions.current()

    def status(self) -> Any:
        """Where the session can still write, and why not elsewhere (:meth:`Sessions.status`)."""
        return self.client.sessions.status()

    def trail(self, *, limit: int | None = None, cursor: str | None = None) -> Any:
        """One page of what the session read, newest first (:meth:`Sessions.trail`)."""
        return self.client.sessions.trail(self.id, limit=limit, cursor=cursor)

    def end(self) -> Any:
        """End the session now. Its id is used up: requests with it answer ``SessionEndedError``."""
        return self.client.sessions.end(self.id) if self.id else None


def _current_trail(limit: int | None, cursor: str | None) -> Flow:
    """One page of the read trail of the session the requesting client runs in."""
    current = yield ops.build_sessions_current_session()
    return (yield ops.build_sessions_session_trail(str(current["id"]), limit=limit, cursor=cursor))


def _current_status() -> Call:
    """The status of the session the requesting client runs in."""
    return ops.build_sessions_current_session_status()


def _end_current() -> Flow:
    """End the session the requesting client runs in."""
    current = yield ops.build_sessions_current_session()
    return (yield ops.build_sessions_end_session(str(current["id"])))


class SessionScope:
    """``with beakr.sessions.start() as s`` (``async with`` on ``AsyncBeakr``): one session."""

    def __init__(self, parent: Any, body: dict[str, Any]) -> None:
        self._parent = parent
        self._body = body
        self._session: AgentSession | None = None

    def _open(self) -> Flow:
        agent_session_id = str(self._body["agent_session"])
        client = session_client(self._parent, agent_session_id)
        call = adjusted(ops.build_sessions_create_session(body=self._body), idempotent=False)  # type: ignore[arg-type]
        try:
            session = yield call
        except (NotFoundError, ValidationError) as exc:
            # A server without sessions (404), or without sessions per
            # conversation (422 feature_not_enabled), starts none; the client
            # still sends the id, which labels its writes as one session.
            if isinstance(exc, ValidationError) and exc.code != "feature_not_enabled":
                raise
            session = None
        self._session = AgentSession(agent_session_id, client, session)
        return self._session

    def __enter__(self) -> AgentSession:
        return self._parent._drive(self._open())  # type: ignore[no-any-return]

    def __exit__(self, *exc_info: object) -> None:
        if self._session is not None:
            self._session.end()

    async def __aenter__(self) -> AgentSession:
        return await self._parent._drive(self._open())  # type: ignore[no-any-return]

    async def __aexit__(self, *exc_info: object) -> None:
        if self._session is not None and self._session.id:
            await self._session.end()


class Sessions(Resource[M]):
    """``/v1/sessions``."""

    def create(
        self,
        *,
        read: builtins.list[str] | None = None,
        write: builtins.list[str] | None = None,
        label: str | None = None,
        folder: str | None = None,
        client_name: str | None = None,
        ttl_seconds: int | None = None,
        read_everything: bool | None = None,
    ) -> Any:
        """Start a session and return a :class:`ScopedSession` with a client confined to it.

        ``read`` and ``write`` are project ids (a picked session); every write
        project is also read (``write`` alone reads and writes the same projects).
        Lists that break a project boundary raise
        :class:`~beakr_sdk.errors.SessionSelectionInvalidError`.
        ``read_everything=True`` reads every project the person can, locked ones
        included, and writes none; it takes no lists. Give none of them for an
        automatic session, or, where the organization makes every session pick, a
        pending one the person chooses at ``picker_url``.
        """
        body = clean_body(
            {
                "client_name": self._credential_client(client_name),
                "label": label,
                "folder": folder,
                "read_project_ids": read,
                "write_project_ids": write,
                "read_everything": read_everything,
                "issue_token": True,
                "ttl_seconds": ttl_seconds,
            }
        )
        if read is None and write is not None:
            body["read_project_ids"] = builtins.list(write)
        call = adjusted(ops.build_sessions_create_session(body=body), idempotent=False)  # type: ignore[arg-type]

        def flow() -> Flow:
            session = yield call
            token = session.pop("token", None) or {}
            client = scoped_client(self._client, str(session["id"]), token, ttl=ttl_seconds)
            return ScopedSession(session=session, client=client)

        return self._drive(flow())

    def scoped(self, session_id: str, *, ttl_seconds: int | None = None) -> Any:
        """A :class:`ScopedSession` for a session you started earlier (mints a fresh token)."""
        body = clean_body({"ttl_seconds": ttl_seconds})
        mint = adjusted(
            ops.build_sessions_mint_session_token(session_id, body=body), idempotent=False
        )  # type: ignore[arg-type]

        def flow() -> Flow:
            token = yield mint
            session = yield ops.build_sessions_get_session(session_id)
            client = scoped_client(self._client, str(session_id), token, ttl=ttl_seconds)
            return ScopedSession(session=session, client=client)

        return self._drive(flow())

    @returns_all(ops.build_sessions_list_sessions)
    def list(self, *, include_ended: bool = False) -> Any:
        """Your sessions, newest first; a session's own client sees only that session."""
        return self._list_all(ops.build_sessions_list_sessions(include_ended=include_ended))

    @returns(ops.build_sessions_get_session)
    def get(self, session_id: str) -> Any:
        return self._run(ops.build_sessions_get_session(session_id))

    def start(
        self,
        *,
        label: str | None = None,
        client_name: str | None = None,
        read: builtins.list[str] | None = None,
        write: builtins.list[str] | None = None,
        read_everything: bool | None = None,
    ) -> SessionScope:
        """A fresh session for a block of work, ended when the block ends.

        ``with beakr.sessions.start(label="import") as s:`` makes a new
        ``X-Beakr-Session`` id, starts that session on the server
        (``POST /v1/sessions`` with ``agent_session``) and gives ``s.client``,
        a client that sends the id; leaving the block ends the session. Use it
        for each unit of work that should stay inside its own project. On a
        server without sessions per agent conversation nothing is started and
        ``s.client`` only labels its writes with the id.

        ``read`` and ``write`` (project ids) declare a picked session, and
        ``read_everything=True`` one that reads every project and writes none,
        as :meth:`create` takes them; without them the session is automatic.
        """
        body = clean_body(
            {
                "agent_session": new_session_id(),
                "label": label,
                "client_name": self._credential_client(client_name),
                "read_project_ids": read,
                "write_project_ids": write,
                "read_everything": read_everything,
            }
        )
        if read is None and write is not None:
            body["read_project_ids"] = builtins.list(write)
        return SessionScope(self._client, body)

    @returns(ops.build_sessions_session_options)
    def options(
        self,
        *,
        read: builtins.list[str] | None = None,
        write: builtins.list[str] | None = None,
    ) -> Any:
        """Which projects a picked session could read and write, given the lists so far.

        ``POST /v1/sessions/options``, with your own credential (not a session
        token): ``valid`` and every ``reasons`` entry for the lists given, the
        ``notes`` on writes that will be held for a project's owners, and each
        project you can read with its ``boundary``, ``can_read``/``can_write`` and
        the ``read_reason``/``write_reason`` when it can't.
        """
        body = {"read_project_ids": read or [], "write_project_ids": write or []}
        return self._run(ops.build_sessions_session_options(body=body))  # type: ignore[arg-type]

    @returns(ops.build_sessions_session_status)
    def status(self, session_id: str | None = None) -> Any:
        """Where a session can still write, and why not elsewhere.

        Its ``mode``, ``lock_state`` (and ``locked_to``), what it has read,
        ``write.projects`` it may write (``outcome`` ``write`` or ``held``),
        ``write.refused`` with each project's ``code`` and ``reason``, a one-line
        ``summary`` and its last boundary ``events``. Without ``session_id``, the
        session this client's requests run in (a scoped client's, or the one its
        ``X-Beakr-Session`` joins; ``NotFoundError`` outside one).
        """
        if session_id is not None:
            return self._run(ops.build_sessions_session_status(session_id))
        return self._run(_current_status())

    @returns(ops.build_sessions_current_session)
    def current(self) -> Any:
        """The session this client's requests run in.

        A scoped client's session, or the one its ``X-Beakr-Session`` joins: its
        ``mode``, ``summary``, ``lock_state`` (``open``, ``locked``, ``read_only``),
        ``write_project`` and ``touched_projects`` say what it may still write.
        """
        return self._run(ops.build_sessions_current_session())

    @returns(ops.build_sessions_session_trail)
    def trail(
        self, session_id: str | None = None, *, limit: int | None = None, cursor: str | None = None
    ) -> Any:
        """One page of what a session read, newest first: ``{data, next_cursor, has_more}``.

        Each read has its ``op`` (``search``, ``cat``, ``rows``,
        ``documents:<command>``, ``read_file``, ``research``...), the
        ``projects`` it touched and the ``items`` it returned. Without
        ``session_id``, the session this client's requests run in
        (:meth:`current`). Readable by the session's person, organization
        admins, and owners or admins of a project it read (who see only the
        reads that touched their projects). Needs the read trail turned on (404
        ``feature_not_enabled`` otherwise).
        """
        if session_id is not None:
            return self._run(
                ops.build_sessions_session_trail(session_id, limit=limit, cursor=cursor)
            )
        return self._drive(_current_trail(limit, cursor))

    def iter_trail(self, session_id: str, *, page_size: int | None = None) -> Any:
        """Every read of a session's trail (:meth:`trail`), newest first, following the cursor."""
        return self._iter(ops.build_sessions_session_trail(session_id, limit=page_size))

    @returns(ops.build_sessions_update_session)
    def update(
        self,
        session_id: str,
        *,
        read: builtins.list[str] | None = None,
        write: builtins.list[str] | None = None,
        label: str | None = None,
        remember_folder: bool | None = None,
        read_everything: bool | None = None,
        mode: str | None = None,
    ) -> Any:
        """Set the session's projects (each list replaces the current one).

        From your own client this chooses or changes them; ``read_everything=True``
        makes it read every project and write none, and ``mode="automatic"`` makes
        the server track what it touches instead (not where the organization's
        session mode is ``must_pick``). From the session's scoped
        client it may only drop projects; naming one the session does not have
        raises :class:`~beakr_sdk.errors.SessionWidenRefusedError`. An automatic or
        read_everything session that has touched a project can't be changed
        (:class:`~beakr_sdk.errors.SessionAlreadyStartedError`).
        """
        body = clean_body(
            {
                "read_project_ids": read,
                "write_project_ids": write,
                "label": label,
                "remember_folder": remember_folder,
                "read_everything": read_everything,
                "mode": mode,
            }
        )
        return self._run(ops.build_sessions_update_session(session_id, body=body))  # type: ignore[arg-type]

    @returns(ops.build_sessions_end_session)
    def end(self, session_id: str | None = None) -> Any:
        """End the session; its tokens stop working at once.

        Without ``session_id``, ends the session this client's requests run in
        (:meth:`current`). An ``X-Beakr-Session`` id is used once: after its
        session ends, this client's requests raise
        :class:`~beakr_sdk.errors.SessionEndedError`; make a new client or use
        :meth:`start` to go on.
        """
        if session_id is not None:
            return self._run(ops.build_sessions_end_session(session_id))
        return self._drive(_end_current())

    def mint_token(self, session_id: str, *, ttl_seconds: int | None = None) -> Any:
        """Mint another token for the session: ``{token, token_type, expires_at}``.

        Only with the credential that started the session; a scoped client renews
        its own token, so this is for clients that keep the token themselves.
        """
        body = clean_body({"ttl_seconds": ttl_seconds})
        return self._run(
            adjusted(ops.build_sessions_mint_session_token(session_id, body=body), idempotent=False)  # type: ignore[arg-type]
        )
