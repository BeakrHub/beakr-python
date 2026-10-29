"""Configuration, request building and retry policy shared by both clients.

Nothing here performs I/O. :class:`BaseClient` turns a :class:`Call` into an
``httpx.Request``, decides whether a failure is retried and for how long to
wait, decodes a response, and owns the credential (including when it needs a
refresh). The sync and async clients only add the send/sleep loop.
"""

from __future__ import annotations

import os
import random
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from beakr_sdk._calls import Call, clean_params, oauth_form_call
from beakr_sdk._redact import mask
from beakr_sdk._replay import IDEMPOTENCY_RETRY_CODES
from beakr_sdk._version import __version__
from beakr_sdk.credentials import (
    Credential,
    StoredTokens,
    TokenStore,
    normalize_origin,
    resolve_base_url,
    resolve_credential,
    resolve_org_id,
)
from beakr_sdk.errors import (
    APIConnectionError,
    AuthenticationError,
    BeakrError,
    RateLimitError,
    ServerError,
    parse_retry_after,
    unexpected_response_error,
)

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

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2

#: Statuses where the server did not (or may not have) done the work and the
#: same request can succeed later. Spec W6.1.3: retries on 429 and 5xx. 501
#: and 505 are left out: they say the server will never do it.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: 429 refusals that waiting a few seconds cannot fix: the organization is out of
#: credits, or a usage quota is spent until its window resets. Returned at once.
NOT_RETRIED_429_CODES = frozenset({"credits_exhausted", "quota_exceeded"})

#: Backoff bounds. The first retry waits about half a second, doubling, never
#: more than the cap -- unless the server asks for longer with Retry-After.
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 8.0
#: A Retry-After beyond this is not waited out: the caller gets the 429.
_MAX_RETRY_AFTER_SECONDS = 60.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BaseClient:
    """Shared state and policy. Use :class:`beakr_sdk.Beakr` or ``AsyncBeakr``."""

    # Attached by ``resources.attach_resources``; declared for type checkers.
    keys: Keys
    me: Me
    meta: Meta
    jobs: Jobs
    organizations: Organizations
    members: Members
    org: OrgAdmin
    external_users: ExternalUsers
    labels: Labels
    end_user_tokens: EndUserTokens
    projects: Projects
    files: Files
    bulk_uploads: BulkUploads
    folders: Folders
    knowledge: Knowledge
    runs: Runs
    interviews: Interviews
    threads: Threads
    conversations: Conversations
    engine: Engine
    integrations: Integrations

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client_name: str | None = None,
        agent: str | None = None,
        run: str | None = None,
        session: str | None = None,
        org_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
        token_store: TokenStore | None = None,
    ) -> None:
        self.base_url = resolve_base_url(base_url)
        self.client_name = client_name or f"sdk-python/{__version__}"
        self.agent = agent
        self.run = run
        # Unset means the environment's; "" sends none even when BEAKR_SESSION is set.
        self.session = os.environ.get("BEAKR_SESSION") if session is None else session
        self.org_id = resolve_org_id(org_id)
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._default_headers = dict(default_headers or {})
        self._token_store = token_store or TokenStore()
        self._explicit_api_key = api_key
        self._credential: Credential | None = None
        #: Injectable so tests can pin the clock an HTTP-date ``Retry-After`` is
        #: measured against.
        self._now: Callable[[], datetime] = _utc_now

    def __repr__(self) -> str:
        # Never resolves the credential (that reads the keychain); shows the one
        # in use, else the explicit key, masked either way.
        credential: object = self._credential
        if credential is None:
            credential = mask(self._explicit_api_key) if self._explicit_api_key else "unresolved"
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, org_id={self.org_id!r}, "
            f"credential={credential!r})"
        )

    # -- credentials -------------------------------------------------------

    @property
    def origin(self) -> str:
        """The normalized API origin this client talks to; stored logins are filed by it."""
        return normalize_origin(self.base_url)

    @property
    def credential(self) -> Credential:
        """The resolved credential. Resolution is lazy so unauthenticated calls work.

        A stored login is offered only when it was issued by this client's
        origin (see :mod:`beakr_sdk.credentials`).
        """
        if self._credential is None:
            self._credential = resolve_credential(
                self._explicit_api_key, token_store=self._token_store, base_url=self.base_url
            )
        return self._credential

    # -- refresh coordination ------------------------------------------------
    #
    # A device-flow refresh rotates the pair, and the engine revokes the whole
    # login when a spent refresh token is presented again. Several processes
    # (the CLI, hooks, an SDK script) share one stored login, so a stored-login
    # refresh runs under the cross-process ``RefreshLock``: reload the store,
    # adopt a pair another process already rotated, and only otherwise spend
    # the refresh token. A refresh whose answer is lost is never retried.

    def _uses_stored_login(self) -> bool:
        return self.credential.source == "cli_tokens"

    def _adopt_rotated_pair(self) -> bool:
        """Under the refresh lock: take the stored pair if another process rotated ours."""
        current = self.credential.tokens
        stored = self._token_store.load(self.origin)
        if current is None or stored is None:
            return False
        if (stored.refresh_token, stored.refresh_state) == (
            current.refresh_token,
            current.refresh_state,
        ):
            return False
        self.credential.tokens = stored
        return True

    def _still_needs_refresh(self, stale_access_token: str | None) -> bool:
        tokens = self.credential.tokens
        if tokens is None:
            return False
        return tokens.access_token == stale_access_token or tokens.access_expired()

    def _assert_refreshable(self) -> None:
        tokens = self.credential.tokens
        if tokens is not None and tokens.refresh_state == "uncertain":
            raise AuthenticationError(
                "This sign-in cannot be refreshed safely: an earlier refresh may "
                "have succeeded on the server without its answer arriving. Run "
                "`beakr auth login` to sign in again.",
                code="refresh_uncertain",
            )

    def _settle_failed_refresh(self, exc: BeakrError) -> None:
        """Turn a failed refresh into the right error. Always raises.

        - Not sent (connection refused, connect timeout) or refused before any
          work (429): the refresh token is untouched; raise the original error.
        - Ambiguous (read timeout, dropped connection, 5xx): the server may have
          rotated the pair. Retrying would present a possibly spent token and
          revoke the login, so mark the pair ``uncertain`` (persisted, so other
          processes stop too) and ask for a new sign-in.
        - Refused (4xx): the login is expired or revoked.
        """
        if isinstance(exc, RateLimitError):
            raise exc
        if isinstance(exc, APIConnectionError) and isinstance(
            exc.__cause__, (httpx.ConnectError, httpx.ConnectTimeout)
        ):
            raise exc
        if isinstance(exc, (APIConnectionError, ServerError)):
            tokens = self.credential.tokens
            if tokens is not None:
                tokens.refresh_state = "uncertain"
                if self._uses_stored_login():
                    self._token_store.save(tokens)
            raise AuthenticationError(
                "The sign-in refresh did not complete and its outcome is unknown. "
                "Run `beakr auth login` to sign in again.",
                code="refresh_uncertain",
            ) from exc
        # The issuer states why in RFC 6749's ``error`` (``invalid_grant``).
        oauth_error = exc.body.get("error") if isinstance(exc.body, dict) else None
        raise AuthenticationError(
            "Your sign-in has expired or was revoked. Run `beakr auth login`.",
            code=oauth_error if isinstance(oauth_error, str) else exc.code or "refresh_failed",
            status=exc.status,
        ) from exc

    def _needs_proactive_refresh(self) -> bool:
        credential = self.credential
        return credential.tokens is not None and credential.tokens.access_expired()

    def _refresh_call(self) -> Call:
        tokens = self.credential.tokens
        assert tokens is not None, "refresh requested for a credential without tokens"
        if not tokens.token_endpoint:
            # Issued by Beakr's own device flow, which the API no longer serves.
            raise AuthenticationError(
                "This sign-in was made with an older Beakr CLI and cannot be refreshed. "
                "Run `beakr auth login`.",
                code="login_outdated",
            )
        return oauth_form_call(
            tokens.token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": tokens.oauth_client_id or "",
            },
        )

    def _apply_refreshed_tokens(self, data: dict[str, Any]) -> None:
        """Adopt a rotated pair; persist it when it came from the CLI login.

        Refresh rotates: the old refresh token is dead the moment the server
        answers, so failing to store the new one would log the user out.
        """
        credential = self.credential
        previous = credential.tokens
        refreshed = StoredTokens.from_token_response(
            data,
            base_url=previous.base_url if previous else self.base_url,
            client_name=previous.client_name if previous else self.client_name,
        )
        if not refreshed.org_id and previous is not None:
            refreshed.org_id = previous.org_id
        if not refreshed.user and previous is not None:
            refreshed.user = previous.user
        if previous is not None:
            refreshed.issuer = previous.issuer
            refreshed.token_endpoint = previous.token_endpoint
            refreshed.oauth_client_id = previous.oauth_client_id
            refreshed.revocation_endpoint = previous.revocation_endpoint
            # An OAuth refresh may not rotate the refresh token.
            if not refreshed.refresh_token:
                refreshed.refresh_token = previous.refresh_token
        credential.tokens = refreshed
        if credential.source == "cli_tokens":
            self._token_store.save(refreshed)

    # -- request building --------------------------------------------------

    def _headers_for(self, call: Call, idempotency_key: str | None) -> dict[str, str]:
        if call.external:
            return dict(call.headers)
        headers: dict[str, str] = {
            "User-Agent": f"beakr-sdk-python/{__version__}",
            "X-Beakr-Client": self.client_name,
            "Accept": "application/json",
            **self._default_headers,
        }
        if self.agent:
            headers["X-Beakr-Agent"] = self.agent
        if self.run:
            headers["X-Beakr-Run"] = self.run
        if self.session:
            headers["X-Beakr-Session"] = self.session
        if self.org_id:
            headers["X-Org-Id"] = self.org_id
        if call.authenticated:
            headers.update(self.credential.auth_headers())
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        headers.update(call.headers)
        return headers

    def _build_request(self, call: Call, idempotency_key: str | None) -> httpx.Request:
        url = call.path if call.external else f"{self.base_url}{call.path}"
        timeout = max(self.timeout, call.min_timeout or 0.0)
        kwargs: dict[str, Any] = {
            "headers": self._headers_for(call, idempotency_key),
            "extensions": {"timeout": httpx.Timeout(timeout).as_dict()},
        }
        # Only when there are some: httpx replaces a URL's own query string with
        # ``params``, even an empty one, and a presigned S3 URL carries its
        # signature in that query string -- passing ``params={}`` stripped it and
        # every upload PUT was refused by S3.
        params = clean_params(call.params or {})
        if params:
            kwargs["params"] = params
        if call.has_json:
            kwargs["json"] = call.json
        elif call.content is not None:
            kwargs["content"] = call.content
        return httpx.Request(call.method.upper(), url, **kwargs)

    @staticmethod
    def _new_idempotency_key(call: Call) -> str | None:
        return str(uuid.uuid4()) if call.idempotent else None

    # -- retry policy ------------------------------------------------------

    def _retry_delay(
        self,
        call: Call,
        attempt: int,
        response: httpx.Response | None,
    ) -> float | None:
        """Seconds to wait before retrying, or ``None`` to stop.

        ``response is None`` means the request failed in transport. A 429 is
        retried (the server refused before doing work) unless its code says no
        retry soon can succeed (:data:`NOT_RETRIED_429_CODES`). A 5xx or a
        transport failure is retried only when resending cannot duplicate an
        effect: an idempotent HTTP method, a create the engine replays on its
        ``Idempotency-Key`` (``beakr_sdk._replay``; the same key is resent), or
        a route that deduplicates resends itself. A keyed create's 409
        ``idempotency_in_progress``/``idempotency_superseded`` (an earlier
        attempt still holds the key) is waited out the same way.
        """
        if attempt >= self.max_retries:
            return None
        if response is not None:
            if not self._retryable_response(call, response):
                return None
            hinted = parse_retry_after(response.headers.get("retry-after"), now=self._now())
            if hinted is not None:
                return hinted if hinted <= _MAX_RETRY_AFTER_SECONDS else None
        elif not call.retry_safe:
            return None
        backoff = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**attempt))
        # Full jitter keeps many clients that failed together from retrying together.
        return backoff * (0.5 + random.random() / 2)

    @staticmethod
    def _retryable_response(call: Call, response: httpx.Response) -> bool:
        status = response.status_code
        if status == 429:
            return _error_code(response) not in NOT_RETRIED_429_CODES
        if status in RETRY_STATUSES:
            return call.retry_safe
        if status == 409 and call.replays_key:
            return _error_code(response) in IDEMPOTENCY_RETRY_CODES
        return False

    # -- response decoding ---------------------------------------------------

    @staticmethod
    def _decode(call: Call, response: httpx.Response) -> Any:
        if call.response == "headers":
            return {key.lower(): value for key, value in response.headers.items()}
        if call.response == "none" or response.status_code == 204:
            return None
        if call.response == "bytes":
            return response.content
        if call.response == "text":
            return response.text
        if not response.content:
            return None
        # A JSON-typed call that receives a file must not be decoded as JSON:
        # return the bytes rather than raising or corrupting them.
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not _is_textual(content_type):
            return response.content
        try:
            return response.json()
        except ValueError:
            # An HTML page answering 200 for any path: the base URL is not a Beakr API.
            raise unexpected_response_error(response) from None

    def _auth_retry_allowed(self, call: Call, response: httpx.Response, refreshed: bool) -> bool:
        """A 401 on an access token that can be refreshed gets one refresh and resend."""
        return (
            response.status_code == 401
            and call.authenticated
            and not call.external
            and not refreshed
            and self.credential.can_refresh
        )


def _error_code(response: httpx.Response) -> str | None:
    """The error code of a refusal: the envelope's, else a structured ``detail``'s."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for holder in (body.get("error"), body.get("detail")):
        if isinstance(holder, dict) and isinstance(holder.get("code"), str):
            return holder["code"]
    return None


def _is_textual(content_type: str) -> bool:
    return (
        "json" in content_type
        or content_type.startswith("text/")
        or "xml" in content_type
        or "x-www-form-urlencoded" in content_type
    )
