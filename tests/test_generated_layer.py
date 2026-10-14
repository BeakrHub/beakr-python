"""The generated, typed layer of the Python SDK (developer platform W6.1, step 1).

``scripts/generate_python_sdk.py`` turns the public contract into a ``TypedDict``
per schema and a typed method per operation (``beakr.api.<area>.<method>()``,
sync and async). These tests hold it to the contract:

- the committed files are exactly what the generator writes from the contract
  (so a contract change without a regeneration fails);
- every contract operation has a generated method, and nothing else does;
- every generated method, sync and async, sends its own operation through the
  real transport (method and path), with the right auth, body and response kind;
- the generated code type-checks, and misuse of it is caught by a type checker.

The hand-written resources build every request with this layer; the package
has no other (``tests/test_shipped_surface.py`` scans the built wheel).

The contract is read from the engine checkout, else the committed snapshot
(``tests._engine.engine_checkout``).
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from beakr_sdk import AsyncBeakr, Beakr
from beakr_sdk._generated import operations as generated
from tests._engine import engine_checkout

BASE = "https://api.test"
_REPO = Path(__file__).resolve().parents[1]
_GENERATED = _REPO / "src" / "beakr_sdk" / "_generated"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_python_sdk", _REPO / "scripts" / "generate_python_sdk.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_python_sdk"] = module
    spec.loader.exec_module(module)
    return module


def _contract_text() -> str:
    return engine_checkout("docs/api/public-openapi.json").read_text()


def _operations_of(text: str) -> set[tuple[str, str]]:
    spec = json.loads(text)
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }


def _contract_operations() -> set[tuple[str, str]]:
    spec = json.loads(_contract_text())
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }


# -- freshness and coverage ----------------------------------------------------


def test_committed_layer_is_what_the_generator_writes_from_the_contract():
    generator = _generator()
    files = generator.render(_contract_text(), generator.PUBLIC)
    stale = [name for name, text in files.items() if (_GENERATED / name).read_text() != text]
    assert stale == [], f"regenerate with `uv run python scripts/generate_python_sdk.py`: {stale}"


def test_every_contract_operation_has_exactly_one_generated_method():
    generated_ops = list(generated.OPERATIONS.values())
    assert len(generated_ops) == len(set(generated_ops)), "two methods send one operation"
    assert set(generated_ops) == _contract_operations()


def test_every_operation_has_a_builder_and_sync_and_async_methods():
    sync_root = generated.BeakrApi(object())
    async_root = generated.AsyncBeakrApi(object())
    for dotted in generated.OPERATIONS:
        area, name = dotted.split(".")
        assert callable(getattr(generated, f"build_{area}_{name}")), dotted
        assert inspect.isfunction(getattr(type(getattr(sync_root, area)), name)), dotted
        method = getattr(type(getattr(async_root, area)), name)
        sync_method = getattr(type(getattr(sync_root, area)), name)
        # A stream is consumed as it arrives (an async iterator), not awaited.
        assert inspect.iscoroutinefunction(method) or not inspect.iscoroutinefunction(
            sync_method
        ), dotted


# -- every method sends its operation --------------------------------------------


def _sample(annotation: str) -> Any:
    head = annotation.split(" | ")[0]
    if head.startswith("list["):
        return [_sample(head[5:-1])]
    if head.startswith("Literal["):
        return re.match(r"Literal\[(['\"])(.*?)\1", head).group(2) if "'" in head else 1
    return {"int": 1, "float": 1.0, "bool": True}.get(head, "x1")


def _arguments(function: Any) -> tuple[list[Any], dict[str, Any]]:
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for parameter in list(inspect.signature(function).parameters.values())[1:]:
        if parameter.default is not inspect.Parameter.empty:
            continue
        annotation = str(parameter.annotation)
        if parameter.name == "body":
            value: Any = {}
        else:
            value = _sample(annotation)
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
        else:
            args.append(value)
    return args, kwargs


def _respond(request: httpx.Request) -> httpx.Response:
    if request.headers.get("accept") == "text/event-stream":
        body = 'id: 1\ndata: {"type": "stream_complete", "data": {}}\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={"data": [], "has_more": False, "next_cursor": None})


def _matches(method: str, template: str, sent_method: str, sent_path: str) -> bool:
    pattern = "^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(template)) + "$"
    return method == sent_method and re.match(pattern, sent_path) is not None


def _consume(result: Any) -> Any:
    if inspect.isgenerator(result) or hasattr(result, "__next__"):
        return list(result)
    return result


_OPERATIONS = sorted(generated.OPERATIONS.items())


@pytest.mark.parametrize(("dotted", "operation"), _OPERATIONS, ids=[d for d, _ in _OPERATIONS])
def test_sync_method_sends_its_operation(dotted, operation):
    area, name = dotted.split(".")
    beakr = Beakr(api_key="bk_live_test", base_url=BASE)
    beakr._sleep = lambda _seconds: None
    method = getattr(getattr(beakr.api, area), name)
    args, kwargs = _arguments(method.__func__)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        route = router.route().mock(side_effect=_respond)
        _consume(method(*args, **kwargs))
    sent = [(r.request.method, r.request.url.path) for r in route.calls]
    assert any(_matches(*operation, m, p) for m, p in sent), (dotted, operation, sent)
    beakr.close()


async def _drain(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return [item async for item in result]


def test_async_methods_send_their_operations():
    async def run() -> list[str]:
        beakr = AsyncBeakr(api_key="bk_live_test", base_url=BASE)

        async def no_sleep(_seconds: float) -> None:
            return None

        beakr._sleep = no_sleep
        wrong: list[str] = []
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            route = router.route().mock(side_effect=_respond)
            for dotted, operation in _OPERATIONS:
                area, name = dotted.split(".")
                method = getattr(getattr(beakr.api, area), name)
                args, kwargs = _arguments(method.__func__)
                before = len(route.calls)
                await _drain(method(*args, **kwargs))
                sent = [(r.request.method, r.request.url.path) for r in route.calls[before:]]
                if not any(_matches(*operation, m, p) for m, p in sent):
                    wrong.append(f"{dotted}: {sent}")
        await beakr.close()
        return wrong

    assert asyncio.run(run()) == []


# -- request details ---------------------------------------------------------------


@respx.mock
def test_a_typed_create_sends_its_body_with_an_idempotency_key(client):
    route = respx.post(f"{BASE}/v1/projects").respond(201, json={"id": "p1", "name": "Assays"})
    project = client.api.projects.create_project(body={"name": "Assays"})
    assert project["id"] == "p1"
    request = route.calls.last.request
    assert json.loads(request.content) == {"name": "Assays"}
    assert request.headers["Idempotency-Key"]
    assert request.headers["Authorization"] == "Bearer bk_live_test"


@respx.mock
def test_a_path_value_is_one_segment_never_a_path(client):
    route = respx.get(f"{BASE}/v1/projects/a%2Fb").respond(200, json={"id": "x"})
    client.api.projects.get_project("a/b")
    assert route.called


@respx.mock
def test_optional_query_values_left_unset_are_not_sent(client):
    route = respx.get(f"{BASE}/v1/projects").respond(
        200, json={"data": [], "has_more": False, "next_cursor": None}
    )
    client.api.projects.list_projects()
    assert route.calls.last.request.url.query == b""
    client.api.projects.list_projects(member_only=True, limit=5)
    assert dict(route.calls.last.request.url.params) == {"member_only": "true", "limit": "5"}


@respx.mock
def test_the_all_iterator_follows_the_cursor(client):
    pages = [
        {"data": [{"id": "p1"}], "has_more": True, "next_cursor": "c2"},
        {"data": [{"id": "p2"}], "has_more": False, "next_cursor": None},
    ]
    route = respx.get(f"{BASE}/v1/projects").mock(
        side_effect=[httpx.Response(200, json=page) for page in pages]
    )
    assert [p["id"] for p in client.api.projects.list_projects_all()] == ["p1", "p2"]
    assert route.calls[1].request.url.params["cursor"] == "c2"


@respx.mock
def test_an_anonymous_operation_sends_no_credential(client):
    anonymous = [
        (dotted, operation)
        for dotted, operation in _OPERATIONS
        if "authenticated=False"
        in inspect.getsource(getattr(generated, "build_" + dotted.replace(".", "_")))
    ]
    assert anonymous, "the contract has anonymous operations (sign-in, the contract itself)"
    dotted, (method, path) = next(
        (d, op) for d, op in anonymous if "{" not in op[1] and op[0] == "GET"
    )
    route = respx.get(f"{BASE}{path}").respond(200, json={})
    area, name = dotted.split(".")
    getattr(getattr(client.api, area), name)()
    assert "Authorization" not in route.calls.last.request.headers


# -- typing --------------------------------------------------------------------------

_GOOD = """
from beakr_sdk import AsyncBeakr, Beakr
from beakr_sdk._generated import models

beakr = Beakr()
project: models.ProjectOut = beakr.api.projects.create_project(body={"name": "Assays"})
name: str = project["name"]
for item in beakr.api.projects.list_projects_all(member_only=True):
    item_id: str = item["id"]

async def main() -> None:
    other: models.ProjectOut = await AsyncBeakr().api.projects.get_project("p1")
"""

_BAD = """
from beakr_sdk import Beakr

beakr = Beakr()
beakr.api.projects.create_project(body={"name": 1})
beakr.api.projects.get_project()
count: int = beakr.api.projects.get_project("p1")["name"]
"""


def _mypy(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    sample = tmp_path / "sample.py"
    sample.write_text(source)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--python-version",
            "3.10",
            "--follow-imports=silent",
            "--no-incremental",
            str(_GENERATED),
            str(sample),
        ],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )


@pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None and shutil.which("mypy") is None,
    reason="mypy is in the dev extra",
)
def test_the_generated_layer_type_checks_and_catches_misuse(tmp_path):
    good = _mypy(tmp_path, _GOOD)
    assert good.returncode == 0, good.stdout + good.stderr
    bad = _mypy(tmp_path, _BAD)
    assert bad.returncode == 1, bad.stdout
    assert '"name" has type "str"' in bad.stdout
    assert 'Missing positional argument "project_id"' in bad.stdout
    assert 'expression has type "str", variable has type "int"' in bad.stdout


# -- path values (audit C057) --------------------------------------------------------


@pytest.mark.parametrize("value", ["", ".", ".."])
def test_a_dot_or_empty_path_value_is_refused_before_sending(value):
    from beakr_sdk._paths import segment

    with pytest.raises(ValueError):
        segment(value)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        route = router.route().respond(200, json={})
        beakr = Beakr(api_key="bk_live_test", base_url=BASE)
        for call in (
            lambda: beakr.api.projects.get_project(value),
            lambda: beakr.projects.get(value),
            lambda: beakr.org.service_accounts.disable(value, org_id="o1"),
        ):
            with pytest.raises(ValueError):
                call()

        async def via_async() -> None:
            client = AsyncBeakr(api_key="bk_live_test", base_url=BASE)
            for call in (
                lambda: client.api.projects.get_project(value),
                lambda: client.projects.get(value),
            ):
                with pytest.raises(ValueError):
                    await call()
            await client.close()

        asyncio.run(via_async())
        beakr.close()
    assert not route.called


def test_an_ordinary_value_is_one_encoded_segment():
    from beakr_sdk._paths import segment

    assert segment("a/b c") == "a%2Fb%20c"
    assert segment("...") == "..."
    assert segment(7) == "7"


# -- hand-written resources sit on the generated layer ----------------------------

_RESOURCES = _REPO / "src" / "beakr_sdk" / "resources"
_RESOURCE_FILES = sorted(_RESOURCES.glob("*.py"))

#: Hand-written requests that are not to the public API, so no builder exists.
#: Every other request a resource sends is built by a generated builder.
NOT_CONTRACT_CALLS: dict[tuple[str, str], str] = {
    ("_multipart_upload.py", "multipart_upload_flow"): "PUT of each part to its presigned URL",
    ("files.py", "flow"): "GET of the presigned download URL (in `download`)",
}


def _hand_built_calls() -> list[tuple[str, str, ast.Call]]:
    """Every ``Call(...)`` in the resources, with its innermost enclosing function."""
    found = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, filename: str) -> None:
            self.filename = filename
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "Call":
                found.append((self.filename, self.stack[-1] if self.stack else "", node))
            self.generic_visit(node)

    for path in _RESOURCE_FILES:
        Visitor(path.name).visit(ast.parse(path.read_text()))
    return found


def _literal_path(node: ast.Call) -> str | None:
    if len(node.args) < 2:
        return None
    target = node.args[1]
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value
    if isinstance(target, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in target.values)
    return None


def test_no_resource_hand_builds_a_request_the_contract_has_a_builder_for():
    operations = {
        (method, re.sub(r"\{[^}]*\}", "{}", path)) for method, path in _contract_operations()
    }
    hand_built: set[tuple[str, str]] = set()
    for filename, function, node in _hand_built_calls():
        hand_built.add((filename, function))
        path = _literal_path(node)
        method = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
        assert (str(method).upper(), path) not in operations, (
            f"{filename}:{function} builds {method} {path} by hand; use its generated builder"
        )
        external = any(
            k.arg == "external" and isinstance(k.value, ast.Constant) and k.value.value
            for k in node.keywords
        )
        assert external or (filename, function) in NOT_CONTRACT_CALLS, (
            f"{filename}:{function} builds a request by hand; use a generated builder"
        )
    assert sorted(set(NOT_CONTRACT_CALLS) - hand_built) == [], "stale NOT_CONTRACT_CALLS entries"


def test_the_listed_internal_routes_are_still_outside_the_contract():
    operations = {f"{method} {path}" for method, path in _contract_operations()}
    for reason in NOT_CONTRACT_CALLS.values():
        if reason.endswith(" is internal"):
            assert reason.removesuffix(" is internal") not in operations, (
                f"{reason.split(' is ')[0]} is public now: build it with its generated builder"
            )


def test_every_typed_method_records_the_builder_its_body_sends():
    from beakr_sdk._typing import BUILDER_ATTRIBUTE

    typed = 0
    for path in _RESOURCE_FILES:
        tree = ast.parse(path.read_text())
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for method in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                decorators = [
                    d
                    for d in method.decorator_list
                    if isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Name)
                    and d.func.id in {"returns", "returns_all", "iterates"}
                ]
                if not decorators:
                    continue
                typed += 1
                declared = decorators[0].args[0].attr
                sent = [
                    n.func.attr
                    for n in ast.walk(method)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr.startswith("build_")
                ]
                assert sent == [declared], f"{path.name}:{cls.name}.{method.name}"
    assert typed >= 70, typed
    from beakr_sdk.resources.projects import Projects

    assert getattr(Projects.get, BUILDER_ATTRIBUTE) == "build_projects_get_project"


_TYPED_RESOURCES_GOOD = """
from beakr_sdk import AsyncBeakr, Beakr
from beakr_sdk._generated import models

beakr = Beakr()
project: models.ProjectOut = beakr.projects.get("p1")
projects: list[models.ProjectOut] = beakr.projects.list()
service_accounts = beakr.org.service_accounts

async def main() -> None:
    client = AsyncBeakr()
    other: models.ProjectOut = await client.projects.get("p1")
    every: list[models.ProjectOut] = await client.projects.list()
"""

_TYPED_RESOURCES_BAD = """
from beakr_sdk import AsyncBeakr, Beakr

beakr = Beakr()
beakr.projects.get()
count: int = beakr.projects.get("p1")["name"]

async def main() -> None:
    not_awaited: dict[str, str] = AsyncBeakr().projects.get("p1")
"""


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy is in the dev extra")
def test_hand_written_methods_are_typed_for_each_client(tmp_path):
    good = _mypy(tmp_path, _TYPED_RESOURCES_GOOD)
    assert good.returncode == 0, good.stdout + good.stderr
    bad = _mypy(tmp_path, _TYPED_RESOURCES_BAD)
    assert bad.returncode == 1, bad.stdout
    assert "Too few arguments" in bad.stdout
    assert 'expression has type "str", variable has type "int"' in bad.stdout
    assert 'expression has type "Awaitable[ProjectOut]"' in bad.stdout


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy is in the dev extra")
def test_the_sdk_type_checks():
    # ``amr.py`` and ``local/`` are left out: Agent Memory Repo safety work
    # (audit C053/C054) is changing them; they are still imported and checked
    # wherever this code uses them.
    sdk = _REPO / "src" / "beakr_sdk"
    targets = [
        str(path)
        for path in sorted(sdk.rglob("*.py"))
        if path.name != "amr.py" and "local" not in path.relative_to(sdk).parts
    ]
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=silent", *targets],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_generator_runs_when_the_package_cannot_import(monkeypatch) -> None:
    # Regression: the generator imported beakr_sdk._codegen through the package,
    # whose __init__ imports resources that call generated builders. A contract
    # adding an operation a resource already calls (files.read) left the package
    # unimportable until regeneration, so the generator could not run at all.
    monkeypatch.setitem(sys.modules, "beakr_sdk", None)  # any package import now fails
    spec = importlib.util.spec_from_file_location(
        "generate_python_sdk_isolated", _REPO / "scripts" / "generate_python_sdk.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.render) and module.PUBLIC.package == "beakr_sdk._generated"
