"""Context for an agent session, and queueing an imported conversation for compounding.

Example::

    ctx = beakr.engine.context(project_id=project_id, format="claude_md")
    print(ctx["content"])
    beakr.engine.compound(conversation_id)  # 202, runs in the background
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource

CONTEXT_FORMATS = ("json", "markdown", "claude_md", "agents_md")


class Engine(Resource[M]):
    def context(
        self,
        *,
        project_id: str | None = None,
        question: str | None = None,
        since: str | None = None,
        format: str = "markdown",
        budget_chars: int | None = None,
        client: str | None = None,
    ) -> Any:
        """Onboarding context: profiles, memories, recent changes, skills, entities.

        ``client`` names the agent starting the session (``claude-code``,
        ``codex``); each client keeps its own last session for "changes since".
        Omitted, the request's ``X-Beakr-Client`` label is used.

        Returns ``{format, content, blocks: [{name, content}], project_id,
        changes_since, client}``.
        """
        body = clean_body(
            {
                "project_id": project_id,
                "question": question,
                "since": since,
                "format": format,
                "budget_chars": budget_chars,
                "client": client,
            }
        )
        return self._run(ops.build_conversations_agent_context(body=body))

    @returns(ops.build_conversations_compound)
    def compound(self, conversation_id: str) -> Any:
        """Queue an imported conversation for compounding. Returns ``{status, conversation_id}``."""
        return self._run(
            ops.build_conversations_compound(body={"conversation_id": conversation_id})
        )

    @returns(ops.build_conversations_cancel_compound)
    def cancel_compound(self, conversation_id: str) -> Any:
        """Stop a queued or running compound of an imported conversation.

        A batch already being read finishes and its proposals stay; no further
        batch starts, and the conversation's ``compound_status`` reads
        ``cancelled``. :meth:`compound` resumes from where it stopped. A
        conversation with nothing queued or running is refused with the error
        code ``nothing_to_cancel``.
        """
        return self._run(
            ops.build_conversations_cancel_compound(body={"conversation_id": conversation_id})
        )
