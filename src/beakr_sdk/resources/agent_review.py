"""Agent review: whether a person must accept what API keys and tokens write.

``GET`` / ``PUT /v1/knowledge/agent-review`` and
``PUT /v1/knowledge/agent-review/projects/{project_id}``. The policy has three
fields:

- ``agent_proposals_need_a_person``: a key or token without ``proposals:accept``
  may stage proposals but not accept them or commit Knowledge any other way
  (``PersonReviewRequiredError``);
- ``agent_uploads_need_a_person``: a file a key or token uploads or sends to
  Knowledge is indexed but waits (``knowledge_status: waiting_for_person``)
  until ``files.approve_knowledge`` compiles it;
- ``capture_auto_approve_allowed``: with proposals held, a person's own
  auto-approve still applies to capture suggestions.

A project follows the organization for every field it does not set. Changes
merge: a field left out keeps its value and ``None`` unsets it. Changing the
policy needs a signed-in person or a key holding ``proposals:accept``
(``SessionRequiredError`` otherwise).

Example::

    beakr.knowledge.agent_review.set(agent_proposals_need_a_person=True)
    beakr.knowledge.agent_review.set_project(project_id, agent_uploads_need_a_person=True)
    beakr.knowledge.agent_review.get(project_id=project_id)["effective"]
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource


class AgentReview(Resource[M]):
    """``/v1/knowledge/agent-review``."""

    @returns(ops.build_knowledge_get_agent_review_settings)
    def get(self, *, project_id: str | None = None) -> Any:
        """``{available, org, project_id, project_override, effective}``.

        ``effective`` is the policy in force for ``project_id`` (the
        organization's without one); ``project_override`` lists only the
        fields the project sets itself.
        """
        return self._run(ops.build_knowledge_get_agent_review_settings(project_id=project_id))

    @returns(ops.build_knowledge_set_org_agent_review)
    def set(self, **changes: bool | None) -> Any:
        """Change the organization's policy (organization admins, ``members:admin``).

        Only the fields passed are sent; ``None`` unsets one.
        """
        return self._run(ops.build_knowledge_set_org_agent_review(body=dict(changes)))

    @returns(ops.build_knowledge_set_project_agent_review)
    def set_project(self, project_id: str, **changes: bool | None) -> Any:
        """Change a project's own policy (project admins, ``projects:admin``).

        Only the fields passed are sent; ``None`` makes that field follow the
        organization again.
        """
        return self._run(
            ops.build_knowledge_set_project_agent_review(project_id, body={"policy": dict(changes)})
        )

    @returns(ops.build_knowledge_set_project_agent_review)
    def follow_org(self, project_id: str) -> Any:
        """Drop the project's own policy: it follows the organization for every field."""
        return self._run(
            ops.build_knowledge_set_project_agent_review(project_id, body={"policy": None})
        )
