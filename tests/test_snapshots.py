"""The committed snapshots are what the manifest says, and hold nothing private.

CI reads ``snapshots/beakr-engine`` and ``snapshots/beakr-typescript`` instead
of checking those private repositories out (``scripts/refresh_snapshots.py``).
These tests keep them honest: every file is the one the manifest pins, the
engine's internal contract is never in them, and ``verify`` notices a hand edit.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "refresh_snapshots", _REPO / "scripts" / "refresh_snapshots.py"
)
assert _spec and _spec.loader
snapshots = importlib.util.module_from_spec(_spec)
sys.modules["refresh_snapshots"] = snapshots
_spec.loader.exec_module(snapshots)


def test_the_committed_snapshots_match_their_manifest():
    assert snapshots.verify(release_commit=snapshots.released_engine_commit()) == []


def test_the_engine_snapshot_holds_only_the_documented_contract():
    assert list(snapshots.SNAPSHOTS.rglob("internal-openapi.json")) == []
    assert list(snapshots.SNAPSHOTS.rglob("*private*")) == []


def test_verify_notices_an_edited_or_extra_file(tmp_path):
    copy = tmp_path / "snapshots"
    shutil.copytree(snapshots.SNAPSHOTS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert snapshots.verify(snapshots=copy) == []
    replay = copy / "beakr-typescript/src/replay.ts"
    replay.write_text(replay.read_text() + "\n")
    (copy / "beakr-engine/docs/api/internal-openapi.json").write_text("{}")
    assert snapshots.verify(snapshots=copy) == [
        f"beakr-engine/{snapshots.INTERNAL} does not match the manifest",
        "beakr-typescript/src/replay.ts does not match the manifest",
    ]


def test_a_missing_release_base_is_reported():
    problems = snapshots.verify(release_commit="0" * 40)
    assert problems == [f"beakr-engine has no release base for commit {'0' * 40}"]
