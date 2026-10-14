"""The built wheel references no API route outside the documented contract.

The public SDK exposes only the documented core. It used to ship a private
layer (``beakr_sdk._internal``) with operations outside the documented contract
for the CLI; that layer now lives in the CLI, filtered to what it calls. This test builds the
wheel exactly as it would be published and scans every file in it (modules,
docstrings, the package metadata and its README) for ``/v1/...`` routes: each
must be a path of the engine's ``docs/api/public-openapi.json``. The TypeScript
SDK's ``test/shipped-surface.test.ts`` holds its package to the same rule.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from tests._engine import engine_checkout

_REPO = Path(__file__).resolve().parents[1]

#: Unlisted routes the documented contract's own prose names (a schema
#: description), so the generated models repeat them in comments. They are
#: text, not callable code; the fix belongs in the engine's descriptions, and
#: each entry must leave this list once the contract stops naming it.
NAMED_BY_CONTRACT_PROSE = ("/v1/assistant/threads/mine/{id}",)

#: A path parameter or an f-string field (one level of nested braces, for
#: ``{_seg(project_id)}``).
_FIELD = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
#: A route: ``/v1`` and its segments, each a name or a field.
_ROUTE = re.compile(rf"/v1(?:/(?:{_FIELD}|[A-Za-z0-9_.\-]+))+")
_UUID = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)")


def _template(route: str) -> str:
    """A route with every path parameter, field and example id reduced to ``{}``."""
    route = _UUID.sub("/{}", route)
    route = re.sub(_FIELD, "{}", route)
    return route.rstrip(".,;:").rstrip("/")


def _contract() -> dict:
    return json.loads(engine_checkout("docs/api/public-openapi.json").read_text())


def _documented() -> set[str]:
    return {_template(path) for path in _contract()["paths"]}


def _matches(route: str, documented: set[str]) -> bool:
    """A route is documented, or is a leading part of one that code extends.

    ``"/v1/projects/" + id`` and prose cut short by a docstring summary
    (``/v1/arti...``) are leading parts; a leading part never ends in a field.
    """
    reduced = _template(route)
    if reduced in documented:
        return True
    return not reduced.endswith("{}") and any(
        template.startswith(reduced) for template in documented
    )


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("uv is needed to build the wheel this test inspects")
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out), str(_REPO)],
        check=True,
        capture_output=True,
    )
    (built,) = out.glob("beakr_sdk-*.whl")
    return built


def _shipped_routes(wheel: Path) -> dict[str, set[str]]:
    """Every route each shipped text file names, by file."""
    found: dict[str, set[str]] = {}
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            try:
                text = archive.read(name).decode("utf-8")
            except UnicodeDecodeError:
                continue
            routes = set(_ROUTE.findall(text))
            if routes:
                found[name] = routes
    return found


def test_the_wheel_ships_no_private_layer(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert not [name for name in names if "/_internal/" in name or name.endswith("_internal.py")]


def test_every_route_in_the_wheel_is_documented(wheel: Path) -> None:
    documented = _documented()
    allowed = {_template(route) for route in NAMED_BY_CONTRACT_PROSE}
    undocumented = {
        name: sorted(
            route
            for route in routes
            if not _matches(route, documented) and _template(route) not in allowed
        )
        for name, routes in _shipped_routes(wheel).items()
    }
    undocumented = {name: routes for name, routes in undocumented.items() if routes}
    assert undocumented == {}, f"routes outside the documented contract: {undocumented}"


def test_each_prose_exception_is_still_named_by_the_contract() -> None:
    text = json.dumps(_contract())
    stale = [route for route in NAMED_BY_CONTRACT_PROSE if route not in text]
    assert stale == [], f"the contract no longer names {stale}; drop them from the list"


def test_the_scan_catches_an_undocumented_route() -> None:
    # Guards the scanner itself: an unlisted route must not pass as documented.
    documented = _documented()
    assert not _matches("/v1/workflows/dispatch", documented)
    assert not _matches("/v1/invitations", documented)
    assert _matches("/v1/projects/{project_id}", documented)
    assert _matches("/v1/projects/{_seg(project_id)}/files", documented) == (
        "/v1/projects/{}/files" in documented
    )
    assert _ROUTE.findall('f"/v1/runs/{_seg(run_id)}/events"') == ["/v1/runs/{_seg(run_id)}/events"]
