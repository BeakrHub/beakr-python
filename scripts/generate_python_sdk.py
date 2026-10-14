"""Generate the Python SDK's typed layer from the Beakr public OpenAPI document.

Writes ``src/beakr_sdk/_generated/`` from the documented public contract
(``docs/api/public-openapi.json``): ``beakr.api`` and the builders every
hand-written resource sends through. The SDK carries nothing else: routes the
API serves without documenting them are not in this package (the Beakr CLI
keeps its own private client for the few it needs). The rendering itself is
:mod:`beakr_sdk._codegen`.

Each layer holds:

- ``models.py``: one ``TypedDict`` per component schema. Responses stay plain
  dicts at runtime (every existing ``result["id"]`` keeps working); the types
  describe them for editors and type checkers.
- ``operations.py``: for every public operation, a request builder
  (``build_<area>_<name>(...) -> Call``) and a typed method on a per-area client,
  sync (``beakr.api.<area>.<name>()``) and async (``await
  async_beakr.api.<area>.<name>()``), all sent through the SDK's own transport.

Source, first match wins: the first argument (a path), ``BEAKR_OPENAPI``,
``$BEAKR_ENGINE_PATH/docs/api/public-openapi.json``, then a sibling engine
checkout (``../beakr-engine``), then the
committed snapshot (``snapshots/beakr-engine``, ``scripts/refresh_snapshots.py``).

Usage::

    uv run python scripts/generate_python_sdk.py [PUBLIC_CONTRACT]
    uv run python scripts/generate_python_sdk.py --check   # exit 1 when stale

The output is deterministic, so ``tests/test_generated_layer.py`` can
regenerate in memory and require the committed files to match.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_codegen() -> ModuleType:
    """``beakr_sdk._codegen`` loaded from its file, not through the package.

    Importing ``beakr_sdk`` runs its ``__init__``, which imports the resources,
    which reference generated builders. When a contract adds an operation a
    resource already calls, that import fails until the layer is generated: the
    generator could not run to fix the very thing stopping it. ``_codegen``
    needs only the standard library, so it is loaded on its own.
    """
    path = ROOT / "src" / "beakr_sdk" / "_codegen.py"
    spec = importlib.util.spec_from_file_location("_beakr_sdk_codegen", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_codegen = _load_codegen()
Target, render = _codegen.Target, _codegen.render

OUT = ROOT / "src" / "beakr_sdk" / "_generated"
CONTRACT = "public-openapi.json"

INIT = '''"""The generated, typed layer of the Python SDK. Do not edit; regenerate with
``uv run python scripts/generate_python_sdk.py``.

- :mod:`beakr_sdk._generated.models`: a ``TypedDict`` per contract schema.
- :mod:`beakr_sdk._generated.operations`: a request builder per operation and the
  per-area clients behind ``beakr.api`` (sync) and ``AsyncBeakr().api`` (async).

It describes the documented public contract only, and so does every request the
hand-written resources build with it.
"""
'''

PUBLIC = Target(
    package="beakr_sdk._generated",
    document="public OpenAPI document",
    init=INIT,
)


def contract_candidates(argv: list[str]) -> list[Path]:
    positional = [a for a in argv if not a.startswith("--")]
    found = [Path(positional[0])] if positional else []
    if os.environ.get("BEAKR_OPENAPI"):
        found.append(Path(os.environ["BEAKR_OPENAPI"]))
    if os.environ.get("BEAKR_ENGINE_PATH"):
        found.append(Path(os.environ["BEAKR_ENGINE_PATH"]) / "docs" / "api" / CONTRACT)
    found += [
        ROOT.parent / "beakr-engine" / "docs" / "api" / CONTRACT,
        # The committed snapshot (scripts/refresh_snapshots.py), what CI reads.
        ROOT / "snapshots" / "beakr-engine" / "docs" / "api" / CONTRACT,
    ]
    return found


def find_contract(argv: list[str]) -> Path | None:
    return next((path for path in contract_candidates(argv) if path.exists()), None)


def main(argv: list[str]) -> int:
    source = find_contract(argv)
    if source is None:
        print(f"No {CONTRACT} found. Pass a path or set BEAKR_OPENAPI.")
        return 1
    files = render(source.read_text(), PUBLIC)
    if "--check" in argv:
        stale = [
            str((OUT / name).relative_to(ROOT))
            for name, text in files.items()
            if _read(OUT / name) != text
        ]
        if stale:
            print(f"stale: {', '.join(stale)}; run scripts/generate_python_sdk.py")
            return 1
        print("generated Python SDK is current")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text)
    count = files["operations.py"].count("\ndef build_")
    print(f"wrote {OUT} ({count} operations) from {source}")
    return 0


def _read(path: Path) -> str | None:
    return path.read_text() if path.exists() else None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
