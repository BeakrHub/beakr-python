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
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from beakr_sdk._http_date import parse_http_date


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
        return " ".join(parts)


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


class NotFoundError(BeakrError):
    """404."""


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
    keeps the full response so the guidance text in it is not lost.
    """

    def __init__(self, message: str, *, result: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.result = result


_DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


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
        return PermissionDeniedError(
            message,
            required_scope=details.get("required_scope") or envelope.get("required_scope"),
            **common,
        )
    if status == 404:
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
