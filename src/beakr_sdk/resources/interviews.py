"""Interviews: send someone a link, then read what they said.

One call creates the interview and its link; hand the link to the person however
you like (Beakr sends nothing). When they have finished, read the result, or put
it into one or more projects' Knowledge.

Example::

    link = beakr.interviews.create_link(
        "Assay handover", goal="How the binding assay is run", questions=["Controls?"]
    )
    print(link["url"])  # shown once; send it to the person
    result = beakr.interviews.link_result(link["link_id"])
    beakr.interviews.send_to_projects(link["link_id"], [project_id])

A *sendable* is an interview saved for reuse: :meth:`Interviews.create_sendable`
once, then :meth:`Interviews.link_for` one link per person.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource


class Interviews(Resource[M]):
    # --- Sendable interviews (you ask someone) ---------------------------------

    @returns_all(ops.build_interviews_list_sendable_interviews)
    def sendables(self) -> Any:
        return self._list_all(ops.build_interviews_list_sendable_interviews())

    def create_sendable(
        self,
        title: str,
        *,
        goals: str | None = None,
        key_questions: list[str] | None = None,
        supporting_info: str | None = None,
        kind: str | None = None,
        voice: str | None = None,
        evidence_artifact_ids: list[str] | None = None,
        visible_evidence_artifact_ids: list[str] | None = None,
    ) -> Any:
        body = clean_body(
            {
                "title": title,
                "goals": goals,
                "key_questions": key_questions,
                "supporting_info": supporting_info,
                "kind": kind,
                "voice": voice,
                "evidence_artifact_ids": evidence_artifact_ids,
                "visible_evidence_artifact_ids": visible_evidence_artifact_ids,
            }
        )
        return self._run(ops.build_interviews_create_sendable_interview(body=body))

    def create_link(
        self,
        title: str,
        *,
        goal: str | None = None,
        questions: list[str] | None = None,
        recipient_name: str | None = None,
        recipient_email: str | None = None,
        expires_at: str | None = None,
    ) -> Any:
        """Create an interview and one link to it in one call: ``POST /v1/interview/links``.

        Give a ``goal``, ``questions``, or both. ``recipient_name`` and
        ``recipient_email`` only label who the link is for (Beakr contacts no one);
        ``expires_at`` (ISO 8601 with a time zone) defaults to
        14 days. The answer carries ``link_id``, ``sendable_id``, ``url`` (shown
        once) and ``result_url``.
        """
        body = clean_body(
            {
                "title": title,
                "goal": goal,
                "questions": questions,
                "recipient": clean_body({"name": recipient_name, "email": recipient_email}) or None,
                "expires_at": expires_at,
            }
        )
        return self._run(ops.build_interviews_create_interview_link(body=body))

    def link_for(
        self,
        sendable_id: str,
        *,
        label: str | None = None,
        expires_in_days: int | None = None,
        password: str | None = None,
    ) -> Any:
        """A link for one person. The link's token is returned once and never replayed.

        The answer carries ``token`` and a relative ``path`` (``/i/<token>``), not
        a full URL as :meth:`create_link` returns: the page is served by the
        Beakr web app of the installation, so prefix ``path`` with that app's
        address (for example ``https://beakr.example.com``) before sending it.
        """
        body = clean_body(
            {"label": label, "expires_in_days": expires_in_days, "password": password}
        )
        return self._run(ops.build_interviews_create_link(sendable_id, body=body))

    @returns(ops.build_interviews_revoke_link)
    def revoke_link(self, link_id: str) -> Any:
        return self._run(ops.build_interviews_revoke_link(link_id))

    @returns(ops.build_interviews_link_result)
    def link_result(self, link_id: str) -> Any:
        """What the person answered: the note, transcript highlights and uploads."""
        return self._run(ops.build_interviews_link_result(link_id))

    @returns(ops.build_interviews_send_link_to_knowledge)
    def send_to_projects(self, link_id: str, project_ids: list[str]) -> Any:
        """Put a finished interview into each project's Knowledge (one to ten projects).

        It is filed as a document in each project and compiled like any upload.
        """
        body = {"project_ids": list(project_ids)}
        return self._run(ops.build_interviews_send_link_to_knowledge(link_id, body=body))

    @returns(ops.build_interviews_draft_interview_from_reviews)
    def draft_from_reviews(
        self,
        project_id: str,
        *,
        proposal_ids: list[str] | None = None,
        kinds: list[str] | None = None,
        max_questions: int | None = None,
        participant: Mapping[str, Any] | None = None,
        link_expires_in_days: int | None = None,
        title: str | None = None,
        goals: str | None = None,
    ) -> Any:
        """Turn a project's open review flags into one interview: one question per flag.

        By default every open review flag, newest first, up to ``max_questions``
        (30, the interview's cap); flags past it come back in ``skipped`` as
        ``over_question_limit``. ``kinds`` (``"contradiction"``, ``"ambiguity"``,
        ``"unverifiable"``, ``"likely_error"``, ``"other"``) narrows it, and
        ``proposal_ids`` (up to 100) names the flags instead. Needs
        ``interviews:write`` and ``kb:read``.

        Without ``participant`` the interview is a draft a person edits
        (``edit_url``) and sends. ``participant`` is ``{"user_id": ...}`` (a
        member) or ``{"name": ..., "email": ...}`` (either; a
        ``models.ParticipantIn``): the interview then also gets that person's link,
        ``link`` (``link_id``, ``label``, ``expires_at``, ``result_url``) and
        ``link_url``, shown once (absent on a replay, ``secret_redacted``), valid
        for ``link_expires_in_days`` (14). Beakr sends nothing; deliver the link
        yourself. Nothing to ask answers ``interview: None`` with
        ``empty_reason`` and ``message``. Each answer comes back on its flag
        (``interview_answers`` on the proposal) as a suggestion.
        """
        body = clean_body(
            {
                "proposal_ids": [str(p) for p in proposal_ids] if proposal_ids else None,
                "kinds": list(kinds) if kinds else None,
                "max_questions": max_questions,
                "participant": clean_body(dict(participant)) if participant else None,
                "link_expires_in_days": link_expires_in_days,
                "title": title,
                "goals": goals,
            }
        )
        return self._run(ops.build_interviews_draft_interview_from_reviews(project_id, body=body))

    # --- Requests and sessions (interviews of you, or ones you ran) -----------

    # --- Working on requests and sessions --------------------------------------
