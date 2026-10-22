"""Typed errors raised by the Beakr SDK.

Every error carries the HTTP status, the machine-readable ``code`` from the
error envelope, a human message and the request id the server logged the call
under, so a bug report can name the exact request.

The envelope is ``{"detail": <legacy>, "error": {"code", "message", "fix",
"details", "request_id"}}``. ``details`` holds structured context
(``required_scope``, ``expected_base_index``, and for a validation error every
failing field under ``errors``) and is exposed as ``BeakrError.details``.
Servers before it existed put those fields beside ``code`` (they still do, for
compatibility), so ``required_scope`` is read from ``details`` first and the
envelope second.
Routes that predate the envelope send ``detail`` only, so every field falls
back: the message to ``detail``, the request id to the ``X-Request-Id`` response
header, and the code to one derived from status.

A 404 whose body is the router's bare ``{"detail": "Not Found"}`` (no
envelope) is a route this server does not have, not a missing record: a
self-hosted installation leaves out skills, integrations, organization
administration and other hosted areas. It raises :class:`NotProvidedError`, a
:class:`NotFoundError`, which names the area.

An error's ``str()`` and ``repr()`` mask any Beakr key or bearer token in its
message.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from beakr_sdk._http_date import parse_http_date
from beakr_sdk._redact import scrub


class BeakrError(Exception):
    """Base class for every error the SDK raises."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        body: Any = None,
        details: dict[str, Any] | None = None,
        fix: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        #: How to correct the call (``error.fix``), when the server said.
        self.fix = fix
        self.status = status
        self.code = code
        self.request_id = request_id
        self.body = body
        #: Structured context from ``error.details`` (``{}`` when none was sent).
        self.details: dict[str, Any] = details or {}
        #: The HTTP response that was refused, when there was one; set by
        #: :func:`error_from_response`. ``None`` for errors raised locally.
        self.response: httpx.Response | None = None

    def __str__(self) -> str:
        parts = [self.message]
        limit = quota_text(self.details)
        if limit:
            parts.append(f"({limit})")
        if self.status is not None:
            parts.append(f"(HTTP {self.status}")
            parts[-1] += f", {self.code})" if self.code else ")"
        if self.fix:
            parts.append(f"To fix: {self.fix}")
        if self.request_id:
            parts.append(f"[request {self.request_id}]")
        return scrub(" ".join(parts))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({scrub(self.message)!r})"


def quota_text(details: dict[str, Any]) -> str | None:
    """``12 of 10 uploads per day; resets 2026-10-14T00:00:00Z`` from a limit's details.

    A ``quota_exceeded`` refusal names the limit, its window and when it resets
    in ``error.details``; without them the message ("Uploads limit reached")
    does not say how long to wait.
    """
    limit = details.get("limit")
    if limit is None:
        return None
    resource = str(details.get("resource") or "").replace("_", " ")
    current = details.get("current")
    text = f"{current} of {limit}" if current is not None else f"limit {limit}"
    if resource:
        text += f" {resource}"
    if details.get("window"):
        text += f" per {details['window']}"
    if details.get("resets_at"):
        text += f"; resets {details['resets_at']}"
    return text


class APIConnectionError(BeakrError):
    """The request never produced an HTTP response (DNS, refused, timeout)."""


class AuthenticationError(BeakrError):
    """401, or no usable credential was found locally."""


class PermissionDeniedError(BeakrError):
    """403. ``required_scope`` is set when a scoped key lacks the route's scope."""

    def __init__(self, message: str, *, required_scope: str | None = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.required_scope = required_scope


class PersonReviewRequiredError(PermissionDeniedError):
    """403 ``person_review_required``: this project needs a person to accept or commit.

    With agent review on (``knowledge.agent_review``), an API key or token
    without ``proposals:accept`` may stage proposals but not accept them,
    commit Knowledge any other way, or approve a file waiting for a person.
    A signed-in person, or a key holding ``proposals:accept``, can.
    """


class SessionRequiredError(PermissionDeniedError):
    """403 ``session_required``: only a signed-in person (a browser session) may do this.

    Organization security settings, step-up and agent-review policy changes
    refuse API keys and tokens (an agent-review change accepts a key holding
    ``proposals:accept``).
    """


#: 403 codes with their own error type.
_FORBIDDEN_TYPES: dict[str, type[PermissionDeniedError]] = {
    "person_review_required": PersonReviewRequiredError,
    "session_required": SessionRequiredError,
}


class NotFoundError(BeakrError):
    """404."""


class NotProvidedError(NotFoundError):
    """404 for a route this Beakr installation does not have.

    A self-hosted installation leaves out hosted areas (skills, integrations,
    organization members, external users, end-user tokens, service accounts,
    organization administration) and answers them with the router's bare
    ``{"detail": "Not Found"}``; a missing record answers 404 with an error
    envelope and stays a plain :class:`NotFoundError`. The message is "This
    Beakr installation does not provide <area>." ``area`` is that name, or
    ``None`` when the path belongs to no known area and the message names the
    operation (``method`` and ``path``) instead.
    A method that looked up the active organization first names its own area
    when that lookup is refused (:meth:`naming`).
    ``beakr.meta.capabilities().serves(method, path)`` tells the same before
    calling.
    """

    def __init__(
        self,
        message: str,
        *,
        area: str | None = None,
        method: str | None = None,
        path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.area = area
        self.method = method
        self.path = path

    def naming(self, area: str) -> NotProvidedError:
        """This refusal named for ``area``: the area the caller was after.

        A method that first looks up the active organization
        (``GET /v1/me/organization``) to find what it acts on is refused at that
        lookup on an installation without organization administration; the area
        is the method's own (``members.list`` -> organization members), not the
        lookup's route.
        """
        named = NotProvidedError(
            f"This Beakr installation does not provide {area}.",
            area=area,
            method=self.method,
            path=self.path,
            status=self.status,
            code=self.code,
            request_id=self.request_id,
            body=self.body,
            details=self.details,
            fix=self.fix,
        )
        named.response = self.response
        return named


class ConflictError(BeakrError):
    """409."""


class ValidationError(BeakrError):
    """400 or 422: the request was malformed or failed validation."""


class RateLimitError(BeakrError):
    """429. ``retry_after`` is the server's hint in seconds, when it sent one."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(BeakrError):
    """5xx."""


class BeakrUploadInterrupted(BeakrError):
    """A resumable upload stopped on a transient failure; ``upload_id`` resumes it.

    Raised for a lost connection, a server error, rate limiting, a local read
    error, or parts S3 does not hold yet. A refusal that resuming cannot fix (a
    wrong size, a revoked credential, an aborted upload) propagates as its own
    error instead.
    """

    def __init__(self, upload_id: str, path: object, cause: BaseException) -> None:
        super().__init__(
            f"Upload of {path} stopped ({cause}). Resume it with upload id {upload_id}.",
            code="upload_interrupted",
        )
        self.upload_id = upload_id
        self.path = path


class KnowledgeCommandError(BeakrError):
    """A knowledge door command or write returned ``outcome.status == "error"``.

    The door answers HTTP 200 for a tool-level refusal (unknown page, a write
    refused on a non-card page) and states the failure in ``outcome``; ``result``
    keeps the full response so the guidance text in it is not lost. ``status``
    is 200, and ``str()`` names the code without it.
    """

    def __init__(self, message: str, *, result: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.result = result

    def __str__(self) -> str:
        # The refusal arrived in a 200 body, so no HTTP status refused it: the
        # code alone, as the Beakr CLI words it ("No page 'X' (page_not_found)").
        parts = [self.message]
        if self.code:
            parts.append(f"({self.code})")
        if self.fix:
            parts.append(f"To fix: {self.fix}")
        if self.request_id:
            parts.append(f"[request {self.request_id}]")
        return scrub(" ".join(parts))


_DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


#: Areas an installation may leave out, most specific prefix first, named as the
#: Beakr CLI names them (``beakr_cli.capabilities.AREAS``). The CLI also names
#: areas outside the documented contract, which this package never calls.
NOT_PROVIDED_AREAS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("organization members", re.compile(r"^/v1/organizations/[^/]+/members(/|$)")),
    ("external users", re.compile(r"^/v1/organizations/[^/]+/external-users(/|$)")),
    ("service accounts", re.compile(r"^/v1/organizations/[^/]+/service-accounts(/|$)")),
    ("organization administration", re.compile(r"^/v1/organizations(/|$)")),
    ("end-user tokens", re.compile(r"^/v1/auth/end-user-tokens(/|$)")),
    ("integrations", re.compile(r"^/v1/integrations(/|$)")),
    ("skills", re.compile(r"^/v1/(me/)?skills(/|$)")),
)


def not_provided_area(path: str) -> str | None:
    """The name of the area an API path belongs to, or ``None``."""
    path = api_path(path)
    for name, pattern in NOT_PROVIDED_AREAS:
        if pattern.match(path):
            return name
    return None


def api_path(url_path: str) -> str:
    """The API path of a request URL's path (``/v1/...``), past any base-URL prefix."""
    index = url_path.find("/v1/")
    return url_path[index:] if index >= 0 else url_path


def is_bare_not_found(status: int, body: Any) -> bool:
    """A 404 for a route the server does not have: ``{"detail": "Not Found"}``, no envelope.

    A missing record answers 404 too, but with an error envelope and its own
    message ("Project not found"); only the router's own reply is this bare.
    """
    return (
        status == 404
        and isinstance(body, dict)
        and body.get("detail") == "Not Found"
        and "error" not in body
    )


def _not_provided(response: httpx.Response, common: dict[str, Any]) -> NotProvidedError:
    try:
        request = response.request
        method, path = request.method, api_path(request.url.path)
    except RuntimeError:  # a response built without its request
        method, path = "", ""
    area = not_provided_area(path) if path else None
    named = area or (f"{method} {path}" if path else "this operation")
    return NotProvidedError(
        f"This Beakr installation does not provide {named}.",
        area=area,
        method=method or None,
        path=path or None,
        **common,
    )


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header, or ``None`` when absent or malformed.

    The header is either a number of seconds or an HTTP-date (RFC 9110 section
    10.2.3); a date becomes the seconds from ``now`` (default: the current UTC
    time) until it. A date already past means "retry now" (``0.0``), never a
    negative wait. A value that is neither is ignored, so the caller falls back
    to its own backoff.
    """
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        reference = now if now is not None else datetime.now(timezone.utc)
        deadline = parse_http_date(value, now=reference)
        if deadline is None:
            return None
        seconds = (deadline - reference).total_seconds()
    return max(0.0, seconds)


def _detail_message(detail: Any) -> str | None:
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    # FastAPI validation errors are a list of {loc, msg, type}; keep them readable.
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if isinstance(item, dict) and item.get("msg"):
                location = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                messages.append(f"{location}: {item['msg']}" if location else str(item["msg"]))
            else:
                messages.append(str(item))
        return "; ".join(messages)
    return json.dumps(detail, default=str)


def error_from_response(response: httpx.Response, *, now: datetime | None = None) -> BeakrError:
    """Map a non-2xx response to the matching typed error (``error.response`` is set).

    ``now`` is the clock an HTTP-date ``Retry-After`` is measured from.
    """
    error = _typed_error(response, now=now)
    error.response = response
    return error


def _typed_error(response: httpx.Response, *, now: datetime | None) -> BeakrError:
    status = response.status_code
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text or None

    envelope = body.get("error") if isinstance(body, dict) else None
    envelope = envelope if isinstance(envelope, dict) else {}
    detail = body.get("detail") if isinstance(body, dict) else body

    code = envelope.get("code") or _DEFAULT_CODES.get(status)
    if code is None and status >= 500:
        code = "internal_error"
    message = (
        envelope.get("message")
        or _detail_message(detail)
        or response.reason_phrase
        or f"HTTP {status}"
    )
    request_id = envelope.get("request_id") or response.headers.get("x-request-id")
    details = envelope.get("details")
    details = details if isinstance(details, dict) else {}
    fix = envelope.get("fix")
    common: dict[str, Any] = {
        "status": status,
        "code": code,
        "request_id": request_id,
        "body": body,
        "details": details,
        "fix": fix if isinstance(fix, str) and fix else None,
    }

    if status in (400, 422):
        return ValidationError(message, **common)
    if status == 401:
        return AuthenticationError(message, **common)
    if status == 403:
        return _FORBIDDEN_TYPES.get(str(code), PermissionDeniedError)(
            message,
            required_scope=details.get("required_scope") or envelope.get("required_scope"),
            **common,
        )
    if status == 404:
        if is_bare_not_found(status, body):
            return _not_provided(response, common)
        return NotFoundError(message, **common)
    if status == 409:
        return ConflictError(message, **common)
    if status == 429:
        return RateLimitError(
            message,
            retry_after=parse_retry_after(response.headers.get("retry-after"), now=now),
            **common,
        )
    if status >= 500:
        return ServerError(message, **common)
    return BeakrError(message, **common)
