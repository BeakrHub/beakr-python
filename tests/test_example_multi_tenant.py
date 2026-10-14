"""``examples/multi_tenant_product.py`` keeps each user in its customer's group.

Root cause (proof run B6): the example added a user to its customer's label
only when ``external_users.create`` answered ``created: true``. With a lost
tenants store or a recreated label, an existing user was never added back and
its session token reached none of the customer's projects. Adding a member is
idempotent (the engine upserts the membership), so the example adds it on
every sign-in.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
import respx

#: An external user's user_id (a uuid): external users are never named by external_id.
EXTERNAL_USER = "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f"

API = "https://api.test"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "multi_tenant_product.py"


@pytest.fixture
def example(monkeypatch, tmp_path):
    # The example's agent loop uses the Anthropic SDK, which this package does
    # not depend on; the code under test never calls it.
    anthropic = types.ModuleType("anthropic")
    beta = types.ModuleType("anthropic.types.beta")
    for name in ("BetaMessageParam", "BetaToolParam", "BetaToolResultBlockParam"):
        setattr(beta, name, dict)
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.setitem(sys.modules, "anthropic.types", types.ModuleType("anthropic.types"))
    monkeypatch.setitem(sys.modules, "anthropic.types.beta", beta)
    spec = importlib.util.spec_from_file_location("multi_tenant_product", EXAMPLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STORE", tmp_path / "tenants.json")
    return module


@pytest.mark.parametrize("created", [True, False])
@respx.mock
def test_a_signing_in_user_is_put_in_its_customers_group_every_time(example, created):
    from beakr_sdk import Beakr

    respx.post(f"{API}/v1/organizations/org-1/external-users").respond(
        200 if not created else 201, json={"user_id": EXTERNAL_USER, "created": created}
    )
    member = respx.post(f"{API}/v1/labels/l1/members").respond(201, json={"user_id": EXTERNAL_USER})
    respx.post(f"{API}/v1/auth/end-user-tokens").respond(201, json={"token": "eut_1"})
    respx.get(f"{API}/v1/me/organization").respond(200, json={"id": "org-1"})
    store = {"general_project_id": None, "customers": {"acme": {"label_id": "l1"}}, "users": {}}

    with Beakr(api_key="bk_live_test", base_url=API) as backend:
        token = example.session_token(backend, store, "acme", "alice")

    assert token == "eut_1"
    assert member.called
    assert json.loads(member.calls.last.request.content)["user_id"] == EXTERNAL_USER


def test_offboarding_says_delete_is_permanent() -> None:
    source = EXAMPLE.read_text()
    assert "moves it to the trash" not in source
    assert "deletes\n    the project permanently" in source
