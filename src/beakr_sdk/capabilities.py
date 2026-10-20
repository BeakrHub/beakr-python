"""Which documented operations one Beakr server serves.

Not every Beakr installation serves the whole documented contract: a
self-hosted installation leaves out the hosted organization administration,
embedded-mode and integration routes, and answers a bare 404 for them. Every
server describes the operations it actually serves in its own public OpenAPI
document (``GET /v1/public/openapi.json``), built from the routes it runs, so
a client can tell "this installation does not provide that" apart from "that
record does not exist" without trying the call.

:class:`Capabilities` reads that document. ``beakr.meta.capabilities()``
fetches it once per base URL and keeps it for :data:`CAPABILITIES_TTL_SECONDS`.
A server that publishes no document (an older release) is assumed to serve
everything, so the helper can only ever narrow what a client offers, never
invent a refusal.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from beakr_sdk._calls import Flow
from beakr_sdk._generated import operations as ops
from beakr_sdk.errors import BeakrError

__all__ = [
    "CAPABILITIES_TTL_SECONDS",
    "Capabilities",
    "capabilities_flow",
    "forget_capabilities",
]

#: How long a fetched document is trusted before it is read again. Short: an
#: installation that is upgraded in place gains routes without a client restart.
CAPABILITIES_TTL_SECONDS = 300.0

_METHODS = ("get", "post", "put", "patch", "delete")
_PARAMETER = re.compile(r"\{[^{}/]+\}")


def _pattern(template: str) -> re.Pattern[str]:
    """A contract path as a regex: each ``{parameter}`` matches one path segment."""
    parts = _PARAMETER.split(template)
    return re.compile("[^/]+".join(re.escape(part) for part in parts) + r"\Z")


def _normalize(path: str) -> str:
    """``path`` without a query string or trailing slash (``/v1/projects/``)."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path.rstrip("/") or "/"


def _index(operations: Iterable[tuple[str, str]]) -> dict[str, list[re.Pattern[str]]]:
    index: dict[str, list[re.Pattern[str]]] = {}
    for method, template in operations:
        index.setdefault(method.upper(), []).append(_pattern(_normalize(template)))
    return index


def _matches(index: Mapping[str, list[re.Pattern[str]]], method: str, path: str) -> bool:
    path = _normalize(path)
    return any(pattern.match(path) for pattern in index.get(method.upper(), ()))


#: Every operation of the documented contract this SDK was generated from.
_CONTRACT = _index(ops.OPERATIONS.values())


def _document_operations(document: Mapping[str, Any]) -> list[tuple[str, str]]:
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        return []
    return [
        (method.upper(), str(path))
        for path, item in paths.items()
        if isinstance(item, Mapping)
        for method in _METHODS
        if method in item
    ]


class Capabilities:
    """The documented operations one server serves, read from its OpenAPI document.

    Build one with ``beakr.meta.capabilities()``. ``serves`` takes a concrete
    path (``/v1/organizations/<id>/members``) or a contract template
    (``/v1/organizations/{org_id}/members``).
    """

    def __init__(self, document: Mapping[str, Any] | None) -> None:
        operations = _document_operations(document) if isinstance(document, Mapping) else []
        #: Whether the server published a document. Without one every
        #: operation is assumed served.
        self.documented = bool(operations)
        self._served = _index(operations)

    @classmethod
    def unknown(cls) -> Capabilities:
        """For a server that publishes no document: everything is assumed served."""
        return cls(None)

    def serves(self, method: str, path: str) -> bool:
        """Whether this server serves ``method path``.

        ``False`` only when the server published its document and the
        operation is part of the documented contract but missing from it. A
        route outside the documented contract cannot be judged from the
        document and is assumed served, as is everything on a server without
        a document.
        """
        if not self.documented:
            return True
        if _matches(self._served, method, path):
            return True
        return not _matches(_CONTRACT, method, path)

    def unserved(self, operations: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        """The ``(method, path)`` pairs of ``operations`` this server does not serve."""
        return [(method, path) for method, path in operations if not self.serves(method, path)]


_cache: dict[str, tuple[float, Capabilities]] = {}
_cache_lock = threading.Lock()


def _cache_key(base_url: str) -> str:
    return base_url.strip().rstrip("/").lower()


def forget_capabilities(base_url: str | None = None) -> None:
    """Drop the cached document for ``base_url``, or for every server."""
    with _cache_lock:
        if base_url is None:
            _cache.clear()
        else:
            _cache.pop(_cache_key(base_url), None)


def capabilities_flow(
    base_url: str,
    *,
    refresh: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> Flow:
    """A flow returning ``base_url``'s :class:`Capabilities`, fetched at most once per TTL.

    Any failure to read the document (no such route on an older server, the
    server unreachable) is cached as :meth:`Capabilities.unknown` for the same
    TTL, so a client asking before every call does not retry a fetch that
    cannot succeed.
    """
    key = _cache_key(base_url)
    now = clock()
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None and not refresh and now - cached[0] < CAPABILITIES_TTL_SECONDS:
        return cached[1]
    try:
        document = yield ops.build_openapi_public_openapi()
    except BeakrError:
        document = None
    capabilities = Capabilities(document if isinstance(document, Mapping) else None)
    with _cache_lock:
        _cache[key] = (clock(), capabilities)
    return capabilities
