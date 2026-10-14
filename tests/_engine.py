"""Find the checkout a test compares against (the engine, the TypeScript SDK), or say why not.

``BEAKR_ENGINE_PATH`` wins for the engine (the engine's CI points it at the
engine itself; this repository's CI at the committed snapshot); otherwise a
sibling ``beakr-engine``, else the snapshot
``snapshots/beakr-engine`` (``scripts/refresh_snapshots.py``). The TypeScript
SDK is ``BEAKR_TYPESCRIPT_PATH``, else a sibling ``beakr-typescript``, else
``snapshots/beakr-typescript``. With ``BEAKR_REQUIRE_ENGINE=1`` (set in both
CIs) a missing file fails the test instead of skipping it, so a gate that
compares against another repository cannot silently stop comparing.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
SNAPSHOTS = _REPO / "snapshots"

_ENGINE = ("BEAKR_ENGINE_PATH", ("beakr-engine",), "beakr-engine")
_TYPESCRIPT = ("BEAKR_TYPESCRIPT_PATH", ("beakr-typescript",), "beakr-typescript")


def engine_checkout(relative: str) -> Path:
    """``relative`` in the engine (or its snapshot); skips, or fails when required, if absent."""
    return _checkout(relative, *_ENGINE, "engine")


def typescript_checkout(relative: str) -> Path:
    """``relative`` in the TypeScript SDK (or its snapshot); skips or fails if absent."""
    return _checkout(relative, *_TYPESCRIPT, "TypeScript SDK")


def engine_derived(sources: tuple[str, ...], derived: str, compute: Callable[[Path], str]) -> str:
    """``compute`` over an engine checkout holding ``sources``, else the snapshot's ``derived``.

    The snapshot keeps some facts instead of the engine files they come from
    (``scripts/refresh_snapshots.py`` writes them with the same ``compute``).
    """
    env, siblings, snapshot = _ENGINE
    candidates = _candidates(env, siblings, snapshot)
    for root in candidates:
        if all((root / source).exists() for source in sources):
            return compute(root)
        if root.resolve() == (SNAPSHOTS / snapshot).resolve() and (root / derived).exists():
            return (root / derived).read_text()
    _missing(f"no engine checkout with {', '.join(sources)} (looked in {candidates})")
    raise AssertionError("unreachable")


def refresh_snapshots() -> Any:
    """``scripts/refresh_snapshots.py`` as a module (scripts is not a package)."""
    path = _REPO / "scripts" / "refresh_snapshots.py"
    spec = importlib.util.spec_from_file_location("_engine_refresh_snapshots", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def _candidates(env: str, siblings: tuple[str, ...], snapshot: str) -> list[Path]:
    candidates = []
    if os.environ.get(env):
        candidates.append(Path(os.environ[env]))
    candidates += [_REPO.parent / name for name in siblings]
    return [*candidates, SNAPSHOTS / snapshot]


def _missing(message: str) -> None:
    if os.environ.get("BEAKR_REQUIRE_ENGINE") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _checkout(
    relative: str, env: str, siblings: tuple[str, ...], snapshot: str, label: str
) -> Path:
    candidates = _candidates(env, siblings, snapshot)
    for root in candidates:
        if (root / relative).exists():
            return root / relative
    _missing(f"no {label} checkout with {relative} (looked in {[str(c) for c in candidates]})")
    raise AssertionError("unreachable")
