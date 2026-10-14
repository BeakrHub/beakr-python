"""Which documented creates replay a resend that carries the same ``Idempotency-Key``.

The engine guarantees (developer platform W1.2) that a create retried with the
same key and body yields exactly one resource and the original response, even
when the first attempt failed after committing. Its test
``tests/api/test_public_create_idempotency.py`` classifies every public POST:
a keyed create (replayed in that file, in its owner's tests, or by the shared
wrapper pattern) or a POST that is not a create. Only the keyed creates
of the documented contract are listed here; ``tests/test_replaying_creates.py``
compares this list with the engine's classification so the two cannot drift,
and the TypeScript SDK carries the same list (``beakr-typescript/src/replay.ts``).
A client built on this package for other routes (the Beakr CLI's private
client) adds its own keyed creates with :func:`register_replaying_creates`.

A POST that is not listed (a decision such as ``accept``, a token exchange, a
read with a body) is not resent after an ambiguous failure: some of them are
refused on a repeat, and a token refresh resent with the same refresh token is
treated as theft and revokes the session.

The public contract does not yet mark these operations, so the list is curated
from the engine's classification rather than generated from the contract.
"""

from __future__ import annotations

import re

#: POST path templates whose handler replays a keyed resend. Both a route's
#: canonical path and its deprecated alias are listed (they run one handler).
REPLAYING_CREATES: tuple[str, ...] = (
    "/v1/bulk/complete",
    "/v1/conversations/import",
    "/v1/folders",
    "/v1/interview/links",
    "/v1/interview/sendables",
    "/v1/interview/sendables/{sendable_id}/links",
    "/v1/knowledge/wiki/write",
    "/v1/labels",
    "/v1/labels/{label_id}/members",
    "/v1/labels/{label_id}/projects",
    "/v1/me/api-keys",
    "/v1/organizations/{org_id}/external-users",
    "/v1/organizations/{org_id}/members",
    "/v1/organizations/{org_id}/service-accounts",
    "/v1/organizations/{org_id}/service-accounts/{account_id}/keys",
    "/v1/projects",
    "/v1/projects/{project_id}/invite",
    "/v1/projects/{project_id}/members",
    "/v1/runs",
    "/v1/uploads",
    "/v1/uploads/{upload_id}/complete",
)

#: Conflict codes a keyed create answers while an earlier attempt with the same
#: key still holds the claim (or was taken over). Both say "retry to receive the
#: result" and carry ``Retry-After``.
IDEMPOTENCY_RETRY_CODES = frozenset({"idempotency_in_progress", "idempotency_superseded"})


def _pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile("[^/]+".join(re.escape(part) for part in parts) + r"/?\Z")


_PATTERNS: list[re.Pattern[str]] = [_pattern(template) for template in REPLAYING_CREATES]


def register_replaying_creates(templates: tuple[str, ...] | list[str]) -> None:
    """Treat more POST path templates as keyed creates the engine replays.

    For a client built on this package that sends routes outside the documented
    contract (the Beakr CLI's private client): its own keyed creates, which this
    package does not list. Registering a template twice is harmless.
    """
    known = {pattern.pattern for pattern in _PATTERNS}
    for template in templates:
        pattern = _pattern(template)
        if pattern.pattern not in known:
            _PATTERNS.append(pattern)
            known.add(pattern.pattern)


def replays_idempotency_key(method: str, path: str) -> bool:
    """Whether a keyed ``method path`` (a concrete path, no query) replays a resend."""
    if method.upper() != "POST":
        return False
    path = path.split("?", 1)[0]
    return any(pattern.match(path) for pattern in _PATTERNS)
