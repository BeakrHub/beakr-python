"""Engine-comparing tests find the engine in CI layouts and fail rather than skip there.

Root cause (Codex C060 follow-up): the compat-rules parity test looked only for a
sibling checkout, ignored BEAKR_ENGINE_PATH, and skipped when absent, so in both
CI layouts (engine at $GITHUB_WORKSPACE/engine, or the engine as the workspace)
the release gate silently stopped comparing.
"""

from __future__ import annotations

import pytest
from _pytest.outcomes import Failed, Skipped

from tests._engine import engine_checkout


def test_the_configured_engine_path_wins(tmp_path, monkeypatch):
    target = tmp_path / "engine" / "beakr/api/public/compat.py"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    monkeypatch.setenv("BEAKR_ENGINE_PATH", str(tmp_path / "engine"))
    assert engine_checkout("beakr/api/public/compat.py") == target


def test_a_missing_engine_skips_locally_and_fails_when_required(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAKR_ENGINE_PATH", str(tmp_path / "nowhere"))
    relative = "no/such/file-anywhere.py"
    monkeypatch.delenv("BEAKR_REQUIRE_ENGINE", raising=False)
    with pytest.raises(Skipped):
        engine_checkout(relative)
    monkeypatch.setenv("BEAKR_REQUIRE_ENGINE", "1")
    with pytest.raises(Failed):
        engine_checkout(relative)


def test_without_a_checkout_the_committed_snapshot_is_read(tmp_path, monkeypatch):
    # Client CI has no engine or TypeScript checkout: comparing tests read the
    # snapshots scripts/refresh_snapshots.py commits, never skip.
    from tests import _engine

    monkeypatch.delenv("BEAKR_ENGINE_PATH", raising=False)
    monkeypatch.delenv("BEAKR_TYPESCRIPT_PATH", raising=False)
    monkeypatch.setattr(_engine, "_REPO", tmp_path / "repo")
    assert engine_checkout("docs/api/public-openapi.json") == (
        _engine.SNAPSHOTS / "beakr-engine/docs/api/public-openapi.json"
    )
    assert _engine.typescript_checkout("src/replay.ts") == (
        _engine.SNAPSHOTS / "beakr-typescript/src/replay.ts"
    )
