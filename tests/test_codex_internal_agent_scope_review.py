"""The documented project selection must constrain every Knowledge read."""

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
import respx

from beakr_sdk import Beakr


@pytest.mark.parametrize(
    "tool,arguments",
    [("read_page", {"page": "Shared title"}), ("search", {"query": "Shared title"})],
)
@respx.mock
def test_internal_agent_read_keeps_selected_project(tool, arguments):
    # Dropping scope on cat lets an org-wide key resolve the same title in a
    # different project than the one the agent was configured to work in.
    path = Path(__file__).resolve().parents[1] / "examples/internal_agent.py"
    spec = importlib.util.spec_from_file_location("codex_internal_agent_scope_example", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    route = respx.post("https://api.test/v1/knowledge/wiki/command").mock(
        return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "result"}]})
    )
    with Beakr(api_key="bk_test_review", base_url="https://api.test") as client:
        agent = module.InternalAgent(client, lambda *_: {}, project_id="selected-project")
        agent.call_tool(tool, arguments)
    assert json.loads(route.calls[0].request.content).get("scope") == "selected-project"
