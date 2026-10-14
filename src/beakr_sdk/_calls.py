"""I/O-free descriptions of requests, shared by the sync and async clients.

Resources describe *what* to send as a :class:`Call`; the client decides how to
send it. A multi-step operation (an upload, the device-flow poll, an export
written to disk) is a :data:`Flow`: a generator that yields ``Call`` and
``Sleep`` steps and receives each result back. The sync client drives a flow
with plain calls, the async client with ``await`` -- so every operation is
written once and both clients behave identically. An error raised by a step is
thrown back into the flow, which lets it handle expected failures such as
``authorization_pending``.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Generic, Literal, TypeVar
from urllib.parse import urlencode

from beakr_sdk._replay import replays_idempotency_key

ResponseKind = Literal["json", "text", "bytes", "none", "stream", "headers"]

_UNSET: Any = object()

#: What the request's response decodes to (types only; nothing checks it at runtime).
R = TypeVar("R", covariant=True)
#: The item type of a paginated list.
I = TypeVar("I", covariant=True)  # noqa: E741 - the conventional name for an item type


@dataclass(frozen=True)
class Call(Generic[R]):
    """One HTTP request to the Beakr API (or, with ``external``, to a presigned URL).

    ``Call[R]``: the generated builders say what the response decodes to, so a
    resource method built on one can be typed by it (``resources._typing``).
    """

    method: str
    path: str
    params: Mapping[str, Any] | None = None
    json: Any = _UNSET
    content: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    #: Send the caller's credential. Off for the device flow and token refresh.
    authenticated: bool = True
    #: ``path`` is a full third-party URL (an S3 presigned URL). No Beakr
    #: headers and no credential are sent: the signature is the authorization,
    #: and leaking a bearer token to a storage host is a credential leak.
    external: bool = False
    #: Attach an ``Idempotency-Key`` header (stable across this call's attempts).
    #: On a create the engine replays (``beakr_sdk._replay``) that makes a resend
    #: after an ambiguous failure safe; on any other POST it is only sent.
    idempotent: bool = False
    #: The route itself deduplicates a resend (the assistant run route keys on
    #: its body ``idempotency_key``), so retrying after an ambiguous failure
    #: cannot create twice.
    server_deduplicated: bool = False
    #: ``headers`` returns the response headers (an upload part's ``ETag``).
    response: ResponseKind = "json"
    #: Wait at least this many seconds for the response, even when the client's
    #: own timeout is shorter (a route that is slow by design, like research).
    min_timeout: float | None = None

    @property
    def has_json(self) -> bool:
        return self.json is not _UNSET

    @property
    def replays_key(self) -> bool:
        """A keyed create the engine replays: a resend with the key returns the first result."""
        if not self.idempotent or self.external:
            return False
        return replays_idempotency_key(self.method, self.path)

    @property
    def retry_safe(self) -> bool:
        """Whether resending after an ambiguous failure cannot duplicate an effect."""
        if self.server_deduplicated or self.replays_key:
            return True
        return self.method.upper() in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}


@dataclass(frozen=True)
class ListCall(Call[Any], Generic[I]):
    """A request for one page of a paginated list whose items are ``I``.

    The page is ``{data: [I, ...], next_cursor, has_more}``; generated builders
    return this for every list operation so whole-list helpers can be typed by
    the item.
    """


@dataclass(frozen=True)
class Sleep:
    """Pause a flow (the device-flow poll interval)."""

    seconds: float


Step = Call | Sleep
Flow = Generator[Step, Any, Any]


def clean_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values and render booleans the way FastAPI parses them."""
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = ("true" if value else "false") if isinstance(value, bool) else value
    return cleaned


def clean_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so optional fields fall back to server defaults."""
    return {key: value for key, value in body.items() if value is not None}


#: What :meth:`beakr_sdk.Beakr.request` can return: decoded JSON, text, raw
#: bytes (a file), nothing, or a stream of server-sent events.
RequestResponse = Literal["json", "text", "bytes", "none", "sse"]


def operation_call(
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    json: Any = _UNSET,
    fields: Mapping[str, str] | None = None,
    files: Mapping[str, tuple[str, bytes, str]] | None = None,
    response: RequestResponse = "json",
    authenticated: bool = True,
) -> Call:
    """One public operation as a :class:`Call`, for :meth:`Beakr.request`.

    ``files`` (name -> ``(filename, data, content type)``) with ``fields`` sends
    ``multipart/form-data``; otherwise ``json`` is the body. Every non-GET call
    carries an ``Idempotency-Key``, stable across this call's retries, so a
    create the engine replays is retried after a 5xx or a lost connection
    without being repeated (``beakr_sdk._replay``).
    ``authenticated=False`` is for operations the contract marks anonymous
    (``security: []``): no credential is resolved or sent.
    """
    if not path.startswith("/v1/"):
        raise ValueError(f"request() takes a public API path under /v1/, not {path!r}")
    content: bytes | None = None
    headers: dict[str, str] = {}
    if files:
        from beakr_sdk._multipart import multipart_body

        content, content_type = multipart_body(dict(fields or {}), dict(files))
        headers["Content-Type"] = content_type
        json = _UNSET
    method = method.upper()
    return Call(
        method,
        path,
        params=clean_params(params) if params else None,
        json=json,
        content=content,
        headers=headers,
        idempotent=method != "GET",
        authenticated=authenticated,
        response="stream" if response == "sse" else response,
    )


def adjusted(call: Call, **changes: Any) -> Call:
    """A generated operation's request with transport details changed.

    For a hand-written helper whose request differs from the operation's
    defaults in how it is sent (a body built as multipart by hand, a response
    read as bytes, no ``Idempotency-Key``): the method and path still come from
    the generated builder, so they cannot drift from the contract.
    """
    return replace(call, **changes)


def with_params(call: Call, params: Mapping[str, Any]) -> Call:
    return replace(call, params=dict(params))


def as_stream(call: Call) -> Call:
    """The same call, sent as a streamed response for server-sent events."""
    return replace(call, response="stream", headers={**call.headers, "Accept": "text/event-stream"})


def oauth_form_call(url: str, form: Mapping[str, str]) -> Call:
    """A form-encoded POST to a third-party OAuth endpoint (Clerk's device flow).

    ``external``: the issuer is not the Beakr API, so no Beakr headers and no
    credential are sent; the request authenticates with its own form fields.
    """
    return Call(
        "POST",
        url,
        content=urlencode(dict(form)).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        authenticated=False,
        external=True,
    )
