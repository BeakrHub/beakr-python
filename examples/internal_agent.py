"""An internal agent built on the Beakr SDK.

The pattern from the "Build an internal agent with the SDK" guide
(the ``internal-agent`` guide in Beakr's developer docs): your own loop,
your own model, and Beakr as the agent's memory.

1. Start every task with ``context``: the project's profile, recent changes and
   the Knowledge most relevant to the task, sized to a budget.
2. Give the model a few Beakr tools: research a question, search and read
   Knowledge, and record a finding. Reads run as the agent's credential, so it
   sees exactly what its key may see.
3. Writes are proposals. ``propose_finding`` stages a dated finding that a
   person accepts in Beakr, and every write carries the agent's name
   (``X-Beakr-Agent``) and this run's id (``X-Beakr-Run``), so the run's writes
   can be reviewed, attributed and undone together.
4. A refused tool call is handed back to the model as text (with the fix the
   error names), never raised out of the loop.

The model is any callable ``model(messages, tools) -> step`` where a step is
``{"tool": name, "input": {...}}`` or ``{"answer": text}``: wrap your model
provider's tool-use API in one. ``ResearchModel`` below needs no model at all;
it is what ``python examples/internal_agent.py "question"`` runs.

Run it with ``BEAKR_API_KEY`` set (a key with ``kb:read`` and ``agent:run``, plus ``kb:write`` to
record findings) and, optionally, ``BEAKR_PROJECT_ID``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from beakr_sdk import Beakr, BeakrError
from beakr_sdk.resources.knowledge import result_text
from beakr_sdk.sources import source_where

Step = dict[str, Any]
Model = Callable[[list[dict[str, str]], list[dict[str, Any]]], Step]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "research",
        "description": "A cited answer across Knowledge, documents and connected services.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "search",
        "description": "Find Knowledge sections about a topic; returns pages and section ids.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_page",
        "description": "Read one Knowledge page (with its sources) by title or id.",
        "input_schema": {
            "type": "object",
            "properties": {"page": {"type": "string"}, "section_id": {"type": "string"}},
            "required": ["page"],
        },
    },
    {
        "name": "propose_finding",
        "description": (
            "Propose a dated finding on a page for a person to review. kind is one of "
            "result, lesson, decision, question, correction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page": {"type": "string"},
                "kind": {"type": "string"},
                "text": {"type": "string"},
                "date": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["page", "kind", "text", "rationale"],
        },
    },
]


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    run: str = ""


class InternalAgent:
    """One task at a time: context first, then tools until the model answers."""

    def __init__(
        self,
        beakr: Beakr,
        model: Model,
        *,
        project_id: str | None = None,
        max_steps: int = 8,
        context_chars: int = 6000,
    ) -> None:
        self.beakr = beakr
        self.model = model
        self.project_id = project_id
        self.max_steps = max_steps
        self.context_chars = context_chars

    def run(self, task: str) -> AgentResult:
        context = self.beakr.context(
            project_id=self.project_id,
            question=task,
            format="markdown",
            budget_chars=self.context_chars,
        )
        messages = [
            {"role": "system", "content": "What Beakr knows:\n\n" + str(context.get("content"))},
            {"role": "user", "content": task},
        ]
        result = AgentResult(answer="", run=str(self.beakr.run or ""))
        for _ in range(self.max_steps):
            step = self.model(messages, TOOLS)
            result.steps.append(step)
            if "answer" in step:
                result.answer = str(step["answer"])
                return result
            observation = self.call_tool(str(step.get("tool")), dict(step.get("input") or {}))
            messages.append({"role": "assistant", "content": json.dumps(step)})
            messages.append({"role": "tool", "content": observation})
        result.answer = "Stopped after the step limit without an answer."
        return result

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Run one tool; a refusal comes back as text the model can act on."""
        kb = self.beakr.knowledge
        scope = self.project_id
        try:
            if name == "research":
                return research_text(kb.research(arguments["question"], project_id=scope))
            if name == "search":
                return result_text(kb.sections(arguments["query"], scope=scope))
            if name == "read_page":
                return result_text(
                    kb.cat(arguments["page"], section_id=arguments.get("section_id"), scope=scope)
                )
            if name == "propose_finding":
                staged = kb.add_finding(
                    arguments["page"],
                    kind=arguments["kind"],
                    text=arguments["text"],
                    date=arguments.get("date"),
                    rationale=arguments["rationale"],
                    scope=scope,
                )
                return "Proposed for review. " + result_text(staged)
            return f"Unknown tool {name!r}. Use one of: " + ", ".join(t["name"] for t in TOOLS)
        except KeyError as missing:
            return f"{name} needs {missing.args[0]!r}."
        except BeakrError as error:
            return f"{name} failed ({error.code}): {error.message}" + _fix_hint(error)


def research_text(result: dict[str, Any]) -> str:
    """A research answer as a person reads it: ``text``, then where each cited document lives.

    ``text`` cites inline by readable reference, ``(memo.pdf › p. 7)``, and
    ``citations`` lists the cited documents in order of first use. ``answer`` is
    the raw text with ``{{key}}`` citation tokens, for a caller that renders its
    own citations: shown as it is, it prints the tokens.
    """
    lines = [str(result.get("text") or "")]
    citations = result.get("citations") or []
    if citations:
        lines += ["", "Sources:", *(f"- {source_where(source)}" for source in citations)]
    return "\n".join(lines)


def _fix_hint(error: BeakrError) -> str:
    """The error envelope's ``fix`` (what to do differently), when the API gave one."""
    body = error.body if isinstance(error.body, dict) else {}
    envelope = body.get("error") if isinstance(body.get("error"), dict) else {}
    fix = envelope.get("fix")
    return f" Fix: {fix}" if fix else ""


class ResearchModel:
    """The smallest useful model: research the task once, answer with what came back."""

    def __call__(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Step:
        observations = [m["content"] for m in messages if m["role"] == "tool"]
        if not observations:
            return {"tool": "research", "input": {"question": messages[-1]["content"]}}
        return {"answer": observations[-1]}


def make_client(*, agent: str = "internal-agent") -> Beakr:
    """A client whose writes name this agent and this run."""
    return Beakr(agent=agent, run=f"{agent}-{uuid.uuid4().hex[:12]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('usage: python examples/internal_agent.py "your question"', file=sys.stderr)
        return 2
    with make_client() as beakr:
        agent = InternalAgent(beakr, ResearchModel(), project_id=os.environ.get("BEAKR_PROJECT_ID"))
        result = agent.run(" ".join(argv[1:]))
    print(result.answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
