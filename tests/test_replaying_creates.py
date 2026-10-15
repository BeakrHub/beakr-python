"""The SDKs' list of creates that replay an ``Idempotency-Key`` matches the engine.

Both SDKs resend a keyed create after a 5xx or a lost connection only because
the engine replays it (W1.2). The list is curated (the public contract does not
mark these operations yet), so it is compared here with the engine's own
classification in ``tests/api/test_public_create_idempotency.py`` -- a create
added there, or a route moved to its not-a-create list, fails this test until
the SDK lists follow -- and the TypeScript SDK's copy (``beakr-typescript``) is
compared with Python's.
"""

from __future__ import annotations

import json
import re

import pytest

from beakr_sdk._generated.operations import OPERATIONS
from beakr_sdk._replay import REPLAYING_CREATES, replays_idempotency_key
from tests._engine import engine_derived, refresh_snapshots, typescript_checkout


def test_the_list_is_the_engines_keyed_creates_with_their_canonical_paths() -> None:
    # The engine's own classification, else (in CI) the snapshot's copy of it.
    snapshots = refresh_snapshots()
    expected = set(
        json.loads(
            engine_derived(
                (snapshots.IDEMPOTENCY_TESTS, snapshots.ALIASES, snapshots.PUBLIC),
                snapshots.REPLAYING_CREATES,
                snapshots.replaying_creates,
            )
        )
    )
    # This package covers the documented contract only (its own POSTs).
    documented = {path for method, path in OPERATIONS.values() if method == "POST"}
    assert sorted(REPLAYING_CREATES) == sorted(expected & documented)


def test_the_typescript_sdk_carries_the_same_list() -> None:
    source = typescript_checkout("src/replay.ts").read_text()
    block = source[source.index("REPLAYING_CREATES") :]
    block = block[: block.index("];")]
    assert sorted(re.findall(r'"(/v1/[^"]*)"', block)) == sorted(REPLAYING_CREATES)


@pytest.mark.parametrize(
    ("method", "path", "replays"),
    [
        ("POST", "/v1/knowledge/wiki/sources/artifact/a1/revert", True),
        ("POST", "/v1/projects", True),
        ("POST", "/v1/projects/", True),
        ("POST", "/v1/projects/p1/members", True),
        ("POST", "/v1/folders", True),
        ("POST", "/v1/uploads/u1/complete", True),
        ("post", "/v1/projects?x=1", True),
        # Not creates, or not keyed by the engine.
        ("POST", "/v1/uploads/u1/parts", False),
        ("POST", "/v1/projects/p1/members/u1", False),
        ("POST", "/v1/skills/s1/proposals/p1/accept", False),
        ("POST", "/v1/auth/token/refresh", False),
        ("POST", "/v1/artifacts/list", False),
        ("POST", "/v1/projectsx", False),
        # Keyed in the engine but not documented: not this package's to resend.
        ("POST", "/v1/outputs", False),
        # Only a POST creates.
        ("PATCH", "/v1/projects", False),
    ],
)
def test_matching_a_concrete_path(method: str, path: str, replays: bool) -> None:
    assert replays_idempotency_key(method, path) is replays


def test_a_client_for_other_routes_can_register_its_keyed_creates(monkeypatch) -> None:
    # The Beakr CLI's private client sends a few undocumented creates the
    # engine also replays; it registers them instead of this package listing them.
    from beakr_sdk import _replay

    monkeypatch.setattr(_replay, "_PATTERNS", list(_replay._PATTERNS))
    assert not replays_idempotency_key("POST", "/v1/outputs")
    _replay.register_replaying_creates(["/v1/outputs", "/v1/outputs"])
    assert replays_idempotency_key("POST", "/v1/outputs")
    assert len(_replay._PATTERNS) == len(REPLAYING_CREATES) + 1
