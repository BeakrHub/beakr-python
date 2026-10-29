"""``examples/internal_agent.py`` runs as the guide describes (spec W9 guides).

The "Build an internal agent with the SDK" guide in Beakr's developer docs embeds this
example; these tests run it against a mocked API so the guide cannot describe
an agent that does not work: context first, tools by name, writes staged as
proposals carrying the agent and run, refusals handed back to the model, and
the model-free ``ResearchModel`` path the script runs from the command line.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
import respx

from beakr_sdk import Beakr
from tests._engine import engine_checkout

API = "https://api.test"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "internal_agent.py"
GUIDE = "docs/developers/guides/internal-agent.md"


def _load():
    spec = importlib.util.spec_from_file_location("internal_agent", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # A dataclass resolves its annotations through sys.modules while the module runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: A research result as the API answers it: ``text`` cites by readable reference,
#: ``answer`` keeps the raw ``{{key}}`` tokens that ``sources`` resolves.
_RESEARCH = {
    "text": "Kd is 38 nM (spr-run-0910.pdf › p. 2).",
    "answer": "Kd is 38 nM {{rag:3f2a9c1e-7b4d10a2e}}.",
    "citations": [
        {
            "name": "spr-run-0910.pdf",
            "filename": "spr-run-0910.pdf",
            "provider": "upload",
            "location": {"file_id": "f-1"},
        }
    ],
    "sources": {"rag:3f2a9c1e-7b4d10a2e": {"source_ref": "beakr-source:v1:x"}},
}


def _text(body: str) -> dict:
    return {"content": [{"type": "text", "text": body}], "outcome": {"status": "ok"}}


@pytest.fixture
def example():
    return _load()


@pytest.fixture
def beakr():
    with Beakr(api_key="bk_live_test", base_url=API, agent="lab-bot", run="lab-bot-r1") as client:
        yield client


def _mock_api():
    context = respx.post(f"{API}/v1/context").mock(
        return_value=httpx.Response(
            200, json={"format": "markdown", "content": "KX2 is the lead.", "blocks": []}
        )
    )

    def command(request):
        body = json.loads(request.content)
        if body["command"] == "cat" and body["page"] == "Missing page":
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "No page named Missing page."}],
                    "outcome": {"status": "error", "error_code": "page_not_found"},
                },
            )
        return httpx.Response(200, json=_text(f"{body['command']} of {body.get('page', '')}"))

    commands = respx.post(f"{API}/v1/knowledge/wiki/command").mock(side_effect=command)
    writes = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=httpx.Response(200, json=_text("Staged proposal pr-1."))
    )
    research = respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=httpx.Response(200, json=_RESEARCH)
    )
    return context, commands, writes, research


class Scripted:
    """A stand-in model that plays a fixed sequence of steps and records what it saw."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen: list[list[dict]] = []

    def __call__(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        assert {tool["name"] for tool in tools} == {
            "research",
            "search",
            "read_page",
            "propose_finding",
        }
        return self.steps.pop(0)


@respx.mock
def test_the_agent_reads_proposes_and_answers(example, beakr) -> None:
    context, commands, writes, _research = _mock_api()
    model = Scripted(
        [
            {"tool": "read_page", "input": {"page": "KX2"}},
            {
                "tool": "propose_finding",
                "input": {
                    "page": "KX2",
                    "kind": "result",
                    "text": "Binds at 38 nM.",
                    "date": "2026-10-09",
                    "rationale": "From the 9 October SPR run.",
                },
            },
            {"answer": "KX2 binds at 38 nM; I proposed the finding."},
        ]
    )

    result = example.InternalAgent(beakr, model, project_id="proj-1").run("What binds KX2?")

    assert result.answer.startswith("KX2 binds at 38 nM")
    assert result.run == "lab-bot-r1"
    context_body = json.loads(context.calls[0].request.content)
    assert context_body["question"] == "What binds KX2?" and context_body["project_id"] == "proj-1"
    assert "KX2 is the lead." in model.seen[0][0]["content"]
    assert json.loads(commands.calls[0].request.content)["command"] == "cat"
    write = writes.calls[0].request
    body = json.loads(write.content)
    assert body["action"] == "add_finding" and body["rationale"] == "From the 9 October SPR run."
    assert write.headers["X-Beakr-Agent"] == "lab-bot"
    assert write.headers["X-Beakr-Run"] == "lab-bot-r1"
    assert "Proposed for review." in model.seen[2][-1]["content"]


@respx.mock
def test_a_refused_tool_call_goes_back_to_the_model(example, beakr) -> None:
    _mock_api()
    model = Scripted(
        [
            {"tool": "read_page", "input": {"page": "Missing page"}},
            {"tool": "propose_finding", "input": {"page": "KX2"}},
            {"tool": "launch", "input": {}},
            {"answer": "Nothing found."},
        ]
    )

    result = example.InternalAgent(beakr, model).run("Read the missing page")

    observations = [m["content"] for m in model.seen[-1] if m["role"] == "tool"]
    assert "page_not_found" in observations[0]
    assert "needs 'kind'" in observations[1]
    assert observations[2].startswith("Unknown tool 'launch'")
    assert result.answer == "Nothing found."


@respx.mock
def test_the_command_line_model_researches_and_answers(example, beakr) -> None:
    _context, _commands, _writes, research = _mock_api()

    result = example.InternalAgent(beakr, example.ResearchModel()).run("What is the Kd?")

    # Root cause of the regression: the example printed ``answer``, whose
    # citations are raw ``{{rag:...}}`` tokens. It shows ``text`` and the
    # cited documents instead.
    assert "{{" not in result.answer
    assert result.answer.startswith("Kd is 38 nM (spr-run-0910.pdf › p. 2).")
    assert "Sources:\n- spr-run-0910.pdf · upload" in result.answer
    assert json.loads(research.calls[0].request.content)["query"] == "What is the Kd?"


@respx.mock
def test_the_agent_stops_at_its_step_limit(example, beakr) -> None:
    _mock_api()
    loop = Scripted([{"tool": "search", "input": {"query": "x"}}] * 3)
    result = example.InternalAgent(beakr, loop, max_steps=3).run("Loop")
    assert result.answer.startswith("Stopped after the step limit")


def test_the_guide_embeds_this_example_unchanged() -> None:
    # The guide is optional: the engine's documented-core cut removed it, and an
    # example outlives its guide. When the engine has it, it must embed this file.
    guide = engine_checkout("docs/api/public-openapi.json").parents[2] / GUIDE
    if not guide.exists():
        pytest.skip(f"the engine has no {GUIDE}")
    blocks = re.findall(r"```python\n(.*?)```", guide.read_text(), flags=re.DOTALL)
    assert EXAMPLE.read_text() in blocks
