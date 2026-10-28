"""Every SDK call the README and the docstrings name is one the client has.

Root cause (sandbox e2e, 2026-10-27): ``Beakr(run=...)``'s docstring said the
run's writes could be taken back with ``knowledge.undo_run(run)``, a method the
SDK never had (the undo route is not in the public contract), so following the
docstring raised ``AttributeError``. Nothing checked that a call named in prose
exists. This test resolves each one against a real client: every
``beakr.<resource>...(`` in the README's Python blocks, and every
````<resource>.<method>```` reference in the package's docstrings whose first
name is one of the client's resources.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from beakr_sdk import Beakr

_REPO = Path(__file__).resolve().parents[1]
_PACKAGE = _REPO / "src" / "beakr_sdk"

_PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
#: ``beakr.files.upload_many(`` in example code.
_CLIENT_CALL = re.compile(r"\bbeakr\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
#: ````knowledge.cat```` or ````beakr.jobs.wait(job)```` in a docstring.
_DOCSTRING_REFERENCE = re.compile(r"``(?:beakr\.)?([a-z_]\w*(?:\.[a-z_]\w*)+)(?:\([^`]*\))?``")
#: Record fields whose first name happens to be a resource's (a source record's
#: ``meta``, not ``beakr.meta``): data paths, not calls.
_RECORD_FIELDS = frozenset({"meta.filed_by"})


def _resolves(client: Beakr, dotted: str) -> bool:
    target: object = client
    for name in dotted.split("."):
        if not hasattr(target, name):
            return False
        target = getattr(target, name)
    return True


def _readme_calls() -> Iterator[str]:
    readme = (_REPO / "README.md").read_text()
    for block in _PYTHON_BLOCK.findall(readme):
        yield from _CLIENT_CALL.findall(block)


def _docstrings() -> Iterator[tuple[Path, str]]:
    for path in sorted(_PACKAGE.rglob("*.py")):
        if "_generated" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                docstring = ast.get_docstring(node)
                if docstring:
                    yield path, docstring


@pytest.fixture
def offline_client() -> Iterator[Beakr]:
    beakr = Beakr(api_key="bk_live_test", base_url="https://api.test")
    yield beakr
    beakr.close()


def test_every_client_call_in_the_readme_exists(offline_client: Beakr) -> None:
    calls = sorted(set(_readme_calls()))
    assert calls, "the README's Python blocks name no beakr.<resource> calls"
    missing = [call for call in calls if not _resolves(offline_client, call)]
    assert not missing, f"README calls the client does not have: {missing}"


def test_every_resource_reference_in_a_docstring_exists(offline_client: Beakr) -> None:
    resources = {name for name in vars(offline_client) if not name.startswith("_")}
    checked: set[str] = set()
    missing: list[str] = []
    for path, docstring in _docstrings():
        for dotted in _DOCSTRING_REFERENCE.findall(docstring):
            if dotted.split(".", 1)[0] not in resources or dotted in _RECORD_FIELDS:
                continue
            checked.add(dotted)
            if not _resolves(offline_client, dotted):
                missing.append(f"{path.relative_to(_REPO)}: {dotted}")
    assert checked, "no docstring names a client resource; the pattern no longer matches"
    assert not missing, f"docstrings name calls the client does not have: {missing}"
