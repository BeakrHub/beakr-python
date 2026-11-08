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
"""

from __future__ import annotations

import builtins
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from beakr_sdk._calls import Call, Flow, adjusted, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.credentials import REFRESH_SKEW_SECONDS
from beakr_sdk.errors import UnexpectedResponseError
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
        session=parent.session or "",
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
    ) -> Any:
        """Start a session and return a :class:`ScopedSession` with a client confined to it.

        ``read`` and ``write`` are project ids; every write project is also read
        (``write`` alone reads and writes the same projects). Give neither to leave
        the choice to the person: the session is pending until they choose at
        ``picker_url``.
        """
        body = clean_body(
            {
                "client_name": self._credential_client(client_name),
                "label": label,
                "folder": folder,
                "read_project_ids": read,
                "write_project_ids": write,
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

    @returns(ops.build_sessions_current_session)
    def current(self) -> Any:
        """The session of the client making the request (a scoped client only)."""
        return self._run(ops.build_sessions_current_session())

    @returns(ops.build_sessions_update_session)
    def update(
        self,
        session_id: str,
        *,
        read: builtins.list[str] | None = None,
        write: builtins.list[str] | None = None,
        label: str | None = None,
        remember_folder: bool | None = None,
    ) -> Any:
        """Set the session's projects (each list replaces the current one).

        From your own client this chooses or changes them. From the session's
        scoped client it may only drop projects; naming one the session does
        not have raises :class:`~beakr_sdk.errors.SessionWidenRefusedError`.
        """
        body = clean_body(
            {
                "read_project_ids": read,
                "write_project_ids": write,
                "label": label,
                "remember_folder": remember_folder,
            }
        )
        return self._run(ops.build_sessions_update_session(session_id, body=body))  # type: ignore[arg-type]

    @returns(ops.build_sessions_end_session)
    def end(self, session_id: str) -> Any:
        """End the session; its tokens stop working at once."""
        return self._run(ops.build_sessions_end_session(session_id))

    def mint_token(self, session_id: str, *, ttl_seconds: int | None = None) -> Any:
        """Mint another token for the session: ``{token, token_type, expires_at}``.

        Only with the credential that started the session; a scoped client renews
        its own token, so this is for clients that keep the token themselves.
        """
        body = clean_body({"ttl_seconds": ttl_seconds})
        return self._run(
            adjusted(ops.build_sessions_mint_session_token(session_id, body=body), idempotent=False)  # type: ignore[arg-type]
        )
