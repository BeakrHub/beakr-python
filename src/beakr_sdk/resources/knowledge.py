"""Knowledge: the knowledge door (read commands, proposed writes) and proposals.

``command()`` and ``write()`` reach every command and action the door has,
with arguments named exactly as the in-product tools name them. The methods
below are typed shortcuts for the common ones.

Example::

    hits = beakr.knowledge.sections("billing owner", scope="Platform")
    card = beakr.knowledge.cat("Stripe", scope="Platform")
    rows = beakr.knowledge.rows(table="facts", entity="Stripe")
    found = beakr.knowledge.documents_search("Stripe order form renewal", project_id="Platform")
    beakr.knowledge.add_fact(
        "Stripe", attribute="contract renewal", value="2027-01-31",
        evidence=found["results"][0]["source_ref"],  # a file you uploaded
        rationale="From the signed order form", scope="Platform",
    )

A write stages a proposal (unless the user's auto-approve applies it); review it
with ``proposals.show`` and apply it with ``proposals.accept``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from beakr_sdk._calls import Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.errors import ConflictError, KnowledgeCommandError
from beakr_sdk.query_spec import QuerySpec
from beakr_sdk.resources._base import Resource, as_list
from beakr_sdk.resources.agent_review import AgentReview
from beakr_sdk.resources.knowledge_pages import KnowledgePages

#: Seconds a research call waits for its answer, whatever the client's timeout:
#: the engine gives the research route up to 150s (an agentic loop that reads
#: documents from connected services), so a 60s client would give up first.
RESEARCH_TIMEOUT = 170.0
#: Seconds a quick research call waits: the engine ends a quick run within 16s.
QUICK_RESEARCH_TIMEOUT = 30.0
#: Seconds drafting a review flag's resolutions may take, whatever the client's
#: timeout: the engine bounds the model call well inside two minutes.
RESOLUTIONS_TIMEOUT = 150.0
#: How a contradiction review is settled on every card it lists.
RESOLVE_OUTCOMES = frozenset({"keep_disputed", "adopt_disputing", "both_valid"})


def find_resolution_option(options: Any, option_id: str) -> dict[str, Any] | None:
    """A drafted option by its id (``A``, ``b`` ...) or its 1-based number, else None."""
    listed = [option for option in options or [] if isinstance(option, dict)]
    wanted = str(option_id or "").strip()
    for option in listed:
        if str(option.get("id") or "").strip().casefold() == wanted.casefold():
            return option
    if wanted.isdigit() and 1 <= int(wanted) <= len(listed):
        return listed[int(wanted) - 1]
    return None


def _section_resolution(
    section_id: str, section_body: str, expected_revision: int, option_id: str | None = None
) -> dict[str, Any]:
    """The body of ``POST .../resolve``: the section's new Markdown, pinned to a revision."""
    return clean_body(
        {
            "section_id": section_id,
            "new_section_body": section_body,
            "expected_revision": expected_revision,
            "option_id": option_id,
        }
    )


def _page_changed(proposal_id: str, expected: int, current: Any) -> ConflictError:
    return ConflictError(
        f"The page changed since revision {expected} (now {current}); nothing was applied. "
        f"Draft the options again with proposals.resolution_options({proposal_id!r}) and "
        "choose again.",
        code="conflict",
    )


def result_text(result: dict[str, Any]) -> str:
    """The door's human-readable rendering (its text content blocks)."""
    blocks = result.get("content")
    if isinstance(blocks, list):
        return "\n".join(
            str(block.get("text"))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        )
    return str(blocks or "")


def _checked(result: Any) -> Any:
    """Raise on a tool-level refusal, which the door reports as HTTP 200."""
    if not isinstance(result, dict):
        return result
    outcome = result.get("outcome")
    if isinstance(outcome, dict) and outcome.get("status") == "error":
        raise KnowledgeCommandError(
            result_text(result) or "Knowledge command failed.",
            code=outcome.get("error_code") or "command_error",
            status=200,
            result=result,
        )
    return result


#: The actions the proposals route stages (``WikiProposalCreate.action`` on the server).
PROPOSAL_ACTIONS = frozenset({"create", "edit", "find_replace", "mv", "archive", "copy"})


class Proposals(Resource[M]):
    """Staged knowledge writes: ``/v1/knowledge/wiki/proposals``."""

    def list(
        self,
        *,
        status: str = "pending",
        project_id: str | None = None,
        limit: int = 50,
        proposal_type: str | None = None,
    ) -> Any:
        """One page of proposals, newest first.

        ``proposal_type`` keeps one kind: ``review`` lists only review flags
        (:meth:`review_flags`), others include ``create``, ``edit``, ``archive``,
        ``mv`` and ``find_replace``.
        """
        return self._run_then(
            ops.build_knowledge_list_proposals(
                status=status, project_id=project_id, limit=limit, proposal_type=proposal_type
            ),
            # The paginated envelope (``data``), or the older ``proposals`` wrapper.
            lambda r: as_list(r, "data", "proposals"),
        )

    def review_flags(
        self, *, project_id: str | None = None, status: str = "pending", limit: int = 50
    ) -> Any:
        """Review flags only (``proposal_type=review``), open ones by default.

        A review flag is a section Knowledge flagged as contradicted, unverifiable
        or unclear; resolve one with :meth:`resolution_options` and :meth:`resolve`.
        """
        return self.list(status=status, project_id=project_id, limit=limit, proposal_type="review")

    @returns(ops.build_knowledge_get_proposal)
    def show(self, proposal_id: str) -> Any:
        return self._run(ops.build_knowledge_get_proposal(proposal_id))

    @returns(ops.build_knowledge_accept_proposal)
    def accept(self, proposal_id: str, *, reviewed_hash: str | None = None) -> Any:
        """Apply a proposal. Returns ``{status, proposal_id, page_ids, ...}``.

        ``reviewed_hash`` is the ``content_hash`` of the :meth:`show` you reviewed,
        passed unchanged: a proposal that changed since is refused (409
        ``proposal_changed``) and nothing is applied, and a proposal that changes a
        card's table rows can require it (422 ``review_required``). This method
        never reads the proposal to fill it in, because a hash from a read made at
        accept time would bind the accept to whatever the proposal is then rather
        than to what was reviewed.
        """
        body = {"reviewed_hash": reviewed_hash} if reviewed_hash is not None else None
        return self._run(ops.build_knowledge_accept_proposal(proposal_id, body=body))

    @returns(ops.build_knowledge_dismiss_proposal)
    def dismiss(self, proposal_id: str) -> Any:
        return self._run(ops.build_knowledge_dismiss_proposal(proposal_id))

    # -- review flags ----------------------------------------------------------
    #
    # A review flag is a proposal of type ``review``: a section Knowledge flagged
    # as contradicted, unverifiable or unclear. Read it with :meth:`conflict`,
    # draft resolutions with :meth:`resolution_options` and apply the one chosen
    # with :meth:`resolve`, pinned to the page revision they were drafted against.

    @returns(ops.build_knowledge_get_proposal_conflict)
    def conflict(self, proposal_id: str) -> Any:
        """A review flag: the flagged section and its current text, why it was
        flagged, the sources on each side and the page's ``current_revision``."""
        return self._run(ops.build_knowledge_get_proposal_conflict(proposal_id))

    @returns(ops.build_knowledge_generate_proposal_conflict_resolutions)
    def resolution_options(self, proposal_id: str) -> Any:
        """Beakr's drafted resolutions for a pending review flag.

        Returns ``{proposal_id, section_id, current_revision, options, cached,
        no_draft_reason}``. Each option has ``id`` (A, B ...), ``label``,
        ``summary`` and ``new_section_body``, plus ``edits`` for a long section
        or ``row_operation`` and ``table_changes`` for a card's Facts or
        Connections table. Drafting uses a model and can take up to two minutes
        the first time (this call waits :data:`RESOLUTIONS_TIMEOUT` seconds);
        the options are kept for ``current_revision``, so asking again returns
        them at once (``cached: true``) until the page changes.
        """
        call = ops.build_knowledge_generate_proposal_conflict_resolutions(proposal_id)
        return self._run(dataclasses.replace(call, min_timeout=RESOLUTIONS_TIMEOUT))

    def resolve(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
        option_id: str | None = None,
        section_body: str | None = None,
    ) -> Any:
        """Resolve a review flag with a drafted option or your own section text.

        Pass exactly one of ``option_id`` (an option's id from
        :meth:`resolution_options`, or its 1-based number) and ``section_body``
        (the flagged section's new Markdown), with the ``current_revision`` the
        options were drafted (or the flag read) against as ``expected_revision``.
        A card-table option is applied with :meth:`resolve_rows`, any other as a
        section rewrite with :meth:`resolve_section`. The page's revision is read
        first: when it has moved past ``expected_revision`` this raises
        :class:`~beakr_sdk.errors.ConflictError` without drafting or applying
        anything, so options you never saw are never applied. Returns ``{status,
        proposal_id, page_id, section_id, new_revision}``.

        Where the project requires a person to accept proposals, a person, or a
        key with the ``proposals:accept`` (reviewer) permission, must apply it, as
        for :meth:`accept`: any other key or token is refused with
        :class:`~beakr_sdk.errors.PersonReviewRequiredError` (403). A scheduled
        job closes flags with a reviewer key.
        """
        if (option_id is None) == (section_body is None):
            raise ValueError("proposals.resolve takes exactly one of option_id or section_body.")

        def flow() -> Flow:
            conflict = yield ops.build_knowledge_get_proposal_conflict(proposal_id)
            current = (conflict or {}).get("current_revision")
            if current != expected_revision:
                raise _page_changed(proposal_id, expected_revision, current)
            if section_body is not None:
                return (
                    yield ops.build_knowledge_resolve_proposal_conflict(
                        proposal_id,
                        body=_section_resolution(
                            str((conflict or {}).get("section_id") or ""),
                            section_body,
                            expected_revision,
                        ),
                    )
                )
            drafted = yield dataclasses.replace(
                ops.build_knowledge_generate_proposal_conflict_resolutions(proposal_id),
                min_timeout=RESOLUTIONS_TIMEOUT,
            )
            drafted = drafted or {}
            if drafted.get("current_revision") != expected_revision:
                raise _page_changed(proposal_id, expected_revision, drafted.get("current_revision"))
            option = find_resolution_option(drafted.get("options"), str(option_id))
            if option is None:
                raise ValueError(
                    f"Option {option_id!r} is not among this flag's drafted options; list "
                    f"them with proposals.resolution_options({proposal_id!r})."
                )
            if isinstance(option.get("row_operation"), dict):
                return (
                    yield ops.build_knowledge_resolve_conflict_with_rows(
                        proposal_id,
                        body={
                            "option_id": option.get("id"),
                            "expected_revision": expected_revision,
                        },
                    )
                )
            return (
                yield ops.build_knowledge_resolve_proposal_conflict(
                    proposal_id,
                    body=_section_resolution(
                        str(drafted.get("section_id") or (conflict or {}).get("section_id") or ""),
                        str(option.get("new_section_body") or ""),
                        expected_revision,
                        option.get("id"),
                    ),
                )
            )

        return self._drive(flow())

    @returns(ops.build_knowledge_resolve_proposal_conflict)
    def resolve_section(
        self,
        proposal_id: str,
        *,
        section_id: str,
        section_body: str,
        expected_revision: int,
        option_id: str | None = None,
    ) -> Any:
        """Rewrite the flagged section and clear the flag (``POST .../resolve``).

        A page that changed since ``expected_revision`` is refused (409,
        :class:`~beakr_sdk.errors.ConflictError`) and nothing is applied.
        :meth:`resolve` reads ``section_id`` and checks the revision for you.

        Where the project requires a person, a person or a ``proposals:accept``
        (reviewer) key must apply it
        (:class:`~beakr_sdk.errors.PersonReviewRequiredError` otherwise).
        """
        return self._run(
            ops.build_knowledge_resolve_proposal_conflict(
                proposal_id,
                body=_section_resolution(section_id, section_body, expected_revision, option_id),
            )
        )

    @returns(ops.build_knowledge_resolve_conflict_with_rows)
    def resolve_rows(self, proposal_id: str, *, option_id: str, expected_revision: int) -> Any:
        """Apply a drafted row option to a flagged card table (``POST .../resolve-rows``).

        ``option_id`` must be one :meth:`resolution_options` returned for
        ``expected_revision``; a card that changed since is refused (409).

        Where the project requires a person, a person or a ``proposals:accept``
        (reviewer) key must apply it
        (:class:`~beakr_sdk.errors.PersonReviewRequiredError` otherwise).
        """
        body = {"option_id": option_id, "expected_revision": expected_revision}
        return self._run(ops.build_knowledge_resolve_conflict_with_rows(proposal_id, body=body))

    @returns(ops.build_knowledge_resolve_conflict_on_every_card)
    def resolve_on_every_card(self, proposal_id: str, outcome: str) -> Any:
        """Settle a contradiction review on every card it lists with one ``outcome``.

        ``keep_disputed`` (the disputing row is deleted), ``adopt_disputing`` (the
        row it disputed is deleted) or ``both_valid`` (both stand). Returns
        ``{status, proposal_id, updated, skipped}``: ``status`` is ``resolved``
        when any card changed, ``unchanged`` when none did (the review stays
        pending), and each skipped card says why.

        Where the project requires a person, a person or a ``proposals:accept``
        (reviewer) key must apply it
        (:class:`~beakr_sdk.errors.PersonReviewRequiredError` otherwise).
        """
        if outcome not in RESOLVE_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {', '.join(sorted(RESOLVE_OUTCOMES))}, not {outcome!r}."
            )
        return self._run(
            ops.build_knowledge_resolve_conflict_on_every_card(
                proposal_id, body={"outcome": outcome}
            )
        )

    def create(
        self,
        action: str,
        *,
        project_id: str,
        rationale: str | None = None,
        **fields: Any,
    ) -> Any:
        """Stage a page-level proposal through the proposals route.

        ``action`` is one of ``create``, ``edit``, ``find_replace``, ``mv``,
        ``archive`` or ``copy``, and ``project_id`` (the project's id, not its
        name) is required. ``create`` takes ``title`` and ``content`` (the page's
        markdown), not ``sections``; ``edit`` takes ``page`` with ``content`` or
        ``patches``; ``find_replace`` takes ``replacements``; ``mv`` and
        ``archive`` take ``page`` or ``pages``. A rationale is optional.

        The Knowledge write door's actions (``new``, ``edit_section``,
        ``add_fact``, ``add_finding`` ...) are not proposal actions here:
        :meth:`Knowledge.write` takes those, accepts a project name as ``scope``,
        and is the usual way to stage a change.
        """
        if action not in PROPOSAL_ACTIONS:
            raise ValueError(
                f"Unknown proposal action {action!r}: use one of "
                f"{', '.join(sorted(PROPOSAL_ACTIONS))}, or knowledge.write({action!r}, ...) "
                "for the Knowledge write door's actions."
            )
        if "scope" in fields:
            # The route has no scope field and would drop it silently (sandbox e2e,
            # 2026-10-25), so a project name passed as scope is refused up front.
            raise TypeError(
                "proposals.create takes project_id (the project's id), not scope; "
                "knowledge.write takes a project name as scope."
            )
        if not project_id:
            raise ValueError("proposals.create needs project_id: the id of the project it is for.")
        body = clean_body(
            {"action": action, "project_id": project_id, "rationale": rationale, **fields}
        )
        return self._run(ops.build_knowledge_create_proposal(body=body))


class Knowledge(Resource[M]):
    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.proposals: Proposals[M] = Proposals(client)
        self.pages: KnowledgePages[M] = KnowledgePages(client)
        self.agent_review: AgentReview[M] = AgentReview(client)

    # -- the door ------------------------------------------------------------

    def command(
        self, command: str, *, scope: str | None = None, check: bool = True, **arguments: Any
    ) -> Any:
        """Run a read command (``cat``, ``sections``, ``grep``, ``rows``, ``timeline``, ...).

        Returns ``{content: [{type, text}], outcome, sources, coverage, next}``. With ``check``
        (the default) a refusal raises :class:`KnowledgeCommandError`.
        """
        body = {**clean_body(arguments), "command": command}
        if scope:
            body["scope"] = scope
        call = ops.build_knowledge_run_wiki_command(body=body)
        return self._run_then(call, _checked) if check else self._run(call)

    def write(
        self, action: str, *, scope: str | None = None, check: bool = True, **arguments: Any
    ) -> Any:
        """Stage a write (``add_fact``, ``edit_section``, ``new``, ...). Every action
        except ``page_type`` needs ``rationale``."""
        body = {**clean_body(arguments), "action": action}
        if scope:
            body["scope"] = scope
        call = ops.build_knowledge_run_wiki_write(body=body)
        return self._run_then(call, _checked) if check else self._run(call)

    def relay_command(
        self,
        command: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        scope: str | None = None,
        check: bool = True,
    ) -> Any:
        """:meth:`command` with the arguments as one mapping, sent as given.

        For a server relaying an agent's tool call: an argument named like a
        keyword here (``scope``, ``check``) still reaches the door, and an
        explicit ``null`` is kept rather than dropped.
        """
        body = {**dict(arguments or {}), "command": command}
        if scope:
            body["scope"] = scope
        call = ops.build_knowledge_run_wiki_command(body=body)
        return self._run_then(call, _checked) if check else self._run(call)

    def relay_write(
        self,
        action: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        scope: str | None = None,
        check: bool = True,
    ) -> Any:
        """:meth:`write` with the arguments as one mapping, sent as given."""
        body = {**dict(arguments or {}), "action": action}
        if scope:
            body["scope"] = scope
        call = ops.build_knowledge_run_wiki_write(body=body)
        return self._run_then(call, _checked) if check else self._run(call)

    # -- reads ---------------------------------------------------------------

    def cat(
        self,
        page: str,
        *,
        section_id: str | None = None,
        outline: bool | None = None,
        rev: int | None = None,
        lines: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Read a page (title, slug, UUID or ``wiki:`` key) with its sources."""
        return self.command(
            "cat",
            page=page,
            section_id=section_id,
            outline=outline,
            rev=rev,
            lines=lines,
            scope=scope,
        )

    def sections(
        self,
        query: str,
        *,
        limit: int | None = None,
        page_type: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Semantic section search; the usual first step."""
        return self.command("sections", query=query, limit=limit, page_type=page_type, scope=scope)

    search = sections

    def grep(
        self,
        query: str,
        *,
        mode: str | None = None,
        limit: int | None = None,
        context_lines: int | None = None,
        matches_per_page: int | None = None,
        page_type: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Literal (default), ``regex``, ``semantic`` or ``history`` search over page text."""
        return self.command(
            "grep",
            query=query,
            mode=mode,
            limit=limit,
            context_lines=context_lines,
            matches_per_page=matches_per_page,
            page_type=page_type,
            scope=scope,
        )

    def query(
        self,
        spec: QuerySpec | str | None = None,
        *,
        sql: str | None = None,
        cursor: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Ask a question of every map card's rows (the ``query`` command), as SQL or a spec.

        ``sql`` is one SELECT, for example ``SELECT value, COUNT(DISTINCT entity) AS
        cards FROM facts AS OF CURRENT_DATE WHERE entity_type = 'matter' AND field =
        'Current status' GROUP BY value ORDER BY cards DESC``. The engine parses it
        into a spec and never runs it as SQL: ``FROM`` facts, connections or
        findings, up to two ``JOIN`` s on ``SAME_CARD(a)`` or ``TARGET_CARD(a)``,
        ``WHERE`` with AND/OR/NOT, ``AS OF`` a day, ``GROUP BY``, ``COUNT``, ``MIN``,
        ``MAX``, ``SUM``, ``AVG``, ``ORDER BY`` and ``LIMIT``. ``spec`` is the same
        query as JSON (see :mod:`beakr_sdk.query_spec`). Send one of them; SQL may
        also be the first argument (``knowledge.query("SELECT ...")``). Only
        cards you can read are queried; ``scope`` narrows them to one project
        (name or id); ``cursor`` continues a previous answer. Where enabled for the
        organization.

        The answer's text opens with what the query was understood as (for ``sql``
        the spec it was translated to, how each name resolved, what was defaulted
        or applied), then the total and a table; its ``query`` holds the same as
        data (``spec``, ``columns``, ``rows``, ``total``, ``next_cursor``). A query
        the engine cannot run raises :class:`KnowledgeCommandError`
        (``invalid_arguments``).
        """
        if isinstance(spec, str):
            # The SQL passed positionally: `dict("SELECT ...")` raised "dictionary
            # update sequence element #0 has length 1" (sandbox run, 2026-11-14).
            if sql is not None:
                raise ValueError("query takes sql or spec, not both and not neither")
            spec, sql = None, spec
        if (spec is None) == (sql is None):
            raise ValueError("query takes sql or spec, not both and not neither")
        arguments: dict[str, Any] = {"sql": sql} if sql is not None else {"spec": dict(spec)}
        return self.command("query", scope=scope, cursor=cursor, **arguments)

    def rows(
        self,
        table: str = "facts",
        *,
        entity: str | None = None,
        attribute: str | None = None,
        query: str | None = None,
        to: str | None = None,
        connected_to: str | None = None,
        as_of: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        current_only: bool | None = None,
        group_by: str | None = None,
        regex: bool | None = None,
        view: str | None = None,
        field_id: str | None = None,
        match: str | None = None,
        after_card: str | None = None,
        order_by: str | None = None,
        descending: bool | None = None,
        page_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_evidence: bool | None = None,
        scope: str | None = None,
    ) -> Any:
        """Every map card's ``facts``, ``connections`` or ``findings`` rows as one table.

        ``table`` is ``facts``, ``connections`` or ``findings``; ``page_type`` and
        ``entity`` narrow the cards. ``to`` keeps Connections pointing at a page;
        ``connected_to`` keeps cards linked to a page in either direction; ``as_of``
        (Facts, a day such as ``2026-01-31``) reads each card's value on that day.
        ``group_by`` answers counts and totals per group instead of rows; ``regex``
        reads ``attribute``, ``to`` and ``query`` as regular expressions; ``view``
        is ``values`` (default), ``fields``, ``groups`` or ``conflicts``;
        ``field_id`` selects one field as the field inventory names it; ``match``
        is ``meaning`` (the default), ``similar`` (words only) or ``exact``, for how
        ``attribute`` finds a field and ``query`` matches values; ``after_card``
        continues a card scan that stopped at its cap.
        """
        return self.command(
            "rows",
            table=table,
            entity=entity,
            attribute=attribute,
            query=query,
            to=to,
            connected_to=connected_to,
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            current_only=current_only,
            group_by=group_by,
            regex=regex,
            view=view,
            field_id=field_id,
            match=match,
            after_card=after_card,
            order_by=order_by,
            descending=descending,
            page_type=page_type,
            limit=limit,
            offset=offset,
            include_evidence=include_evidence,
            scope=scope,
        )

    def timeline(
        self,
        query: str | None = None,
        *,
        page: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        include_content: bool | None = None,
        include_approx: bool | None = None,
        page_type: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Dated events by date range and keyword."""
        return self.command(
            "timeline",
            timeline_query=query,
            page=page,
            start_date=start_date,
            end_date=end_date,
            include_content=include_content,
            include_approx=include_approx,
            page_type=page_type,
            scope=scope,
        )

    @returns(ops.build_knowledge_documents)
    def documents(self, command: str, *, project_id: str | None = None, **arguments: Any) -> Any:
        """Search/read/list original indexed documents; inspect scan notices before counting.

        ``command`` is grep, read, ls or search. read accepts document and explicit
        start_line/end_line. ``project_id`` is a project id or name; without it a
        command covers everything you can read. Feature-gated on the server, with
        the caller's project permissions enforced identically to REST and MCP.
        """
        return self._run(
            ops.build_knowledge_documents(
                body=clean_body(
                    {
                        "command": command,
                        "arguments": arguments,
                        "project_id": project_id,
                    }
                )
            )
        )

    def documents_search(
        self,
        query: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        passages: int | None = None,
        path: str | None = None,
        glob: str | list[str] | None = None,
        type: str | list[str] | None = None,
        document: str | None = None,
        project_id: str | None = None,
    ) -> Any:
        """Passages of the original documents ranked by meaning, grouped by document.

        For when you do not know the words a document uses; ``grep`` finds and
        counts every literal mention. Returns ``{content, sources, results}``:
        ``results`` is one source record per matching document, best first, with
        ``source_ref``, ``location`` (``display_path``, ``file_id``,
        ``version_id``), ``match`` (strong, moderate or weak), ``score`` and its
        best ``passages`` (``source_ref``, ``unit_locator``, ``line_start``/
        ``line_end``, ``excerpt``, ``match``, ``score``). ``limit`` documents per
        page (default 8, max 25) from ``offset``; ``passages`` per document
        (default 2, max 5). Download a result's file with
        ``files.download_link(record["source_ref"])``.
        """
        arguments = {
            key: value
            for key, value in {
                "query": query,
                "limit": limit,
                "offset": offset,
                "passages": passages,
                "path": path,
                "glob": glob,
                "type": type,
                "document": document,
            }.items()
            if value is not None
        }
        return self.documents("search", project_id=project_id, **arguments)

    def research(self, query: str, *, project_id: str | None = None, quick: bool = True) -> Any:
        """A cited answer from Knowledge and documents; ``quick=False`` for the full researcher.

        Show ``text``: the answer with each citation as its readable reference in
        parentheses, such as ``(memo.pdf › p. 7)``. ``answer`` keeps the raw
        ``{{key}}`` citation tokens, which ``sources`` resolves, for a caller that
        renders its own citations.

        Quick by default: the engine answers within 15 seconds from one search
        of Knowledge and uploaded documents (no connected services); the
        answer's ``engine`` is ``quick`` and ``stopped_early`` is true when it is
        the cited results without a written summary. Waits up to
        :data:`QUICK_RESEARCH_TIMEOUT` seconds.

        ``quick=False`` runs the full researcher, which also reads connected
        services (Slack, Drive, Gmail, Calendar, Jira): for questions about
        those services and exhaustive questions. It waits up to
        :data:`RESEARCH_TIMEOUT` seconds (or the client's timeout, if longer):
        the engine allows it up to 150s.
        """
        body = clean_body({"query": query, "project_id": project_id, "quick": quick or None})
        call = ops.build_knowledge_research(body=body)
        timeout = QUICK_RESEARCH_TIMEOUT if quick else RESEARCH_TIMEOUT
        return self._run(dataclasses.replace(call, min_timeout=timeout))

    # -- card writes -----------------------------------------------------------

    def add_fact(
        self,
        page: str,
        *,
        attribute: str | None = None,
        value: str | None = None,
        evidence: str | None = None,
        when: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
        rationale: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """One Facts row on a project-map card (or several with ``rows=[...]``)."""
        return self.write(
            "add_fact",
            page=page,
            attribute=attribute,
            value=value,
            evidence=evidence,
            when=when,
            rows=rows,
            citations=citations,
            rationale=rationale,
            scope=scope,
        )

    def add_connection(
        self,
        page: str,
        *,
        relation: str | None = None,
        to: str | None = None,
        evidence: str | None = None,
        when: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
        rationale: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """One Connections row: a short verb phrase ``relation`` to another card ``to``."""
        return self.write(
            "add_connection",
            page=page,
            relation=relation,
            to=to,
            evidence=evidence,
            when=when,
            rows=rows,
            citations=citations,
            rationale=rationale,
            scope=scope,
        )

    def add_finding(
        self,
        page: str,
        *,
        date: str | None = None,
        kind: str | None = None,
        text: str | None = None,
        evidence: str | None = None,
        about: str | list[str] | None = None,
        finding_scope: str | None = None,
        supersedes: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        rationale: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """One dated Findings bullet; ``kind``: result|lesson|decision|question|correction."""
        return self.write(
            "add_finding",
            page=page,
            date=date,
            kind=kind,
            text=text,
            evidence=evidence,
            about=about,
            finding_scope=finding_scope,
            supersedes=supersedes,
            rows=rows,
            rationale=rationale,
            scope=scope,
        )

    def edit_rows(
        self,
        page: str,
        *,
        table: str,
        old_rows: list[dict[str, Any] | str] | None = None,
        rows: list[dict[str, Any]] | None = None,
        reason: str | None = None,
        edits: list[dict[str, Any]] | None = None,
        rationale: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Edit, merge or delete rows already on a card (``rows=[]`` deletes)."""
        return self.write(
            "edit_rows",
            page=page,
            table=table,
            old_rows=old_rows,
            rows=rows,
            reason=reason,
            edits=edits,
            rationale=rationale,
            scope=scope,
        )

    # -- Knowledge as resources (data, not the door's prose) ------------------

    # -- card types ------------------------------------------------------------

    @returns(ops.build_knowledge_list_wiki_page_types)
    def list_card_types(
        self,
        *,
        project_id: str | None = None,
        include_proposed: bool | None = None,
        include_deprecated: bool | None = None,
        include_counts: bool | None = None,
    ) -> Any:
        """The card types a project uses: ``{page_types, map_mode, settings, kept_types}``.

        Each type is as the narrowest scope that defines it holds it: ``scope`` is
        ``project``, ``org`` or ``global``, and ``metadata_schema`` holds its
        fields (``key_attributes``), which are required (``required_attributes``)
        and whether an instance is a card or a row (``grain``). ``project_id`` is
        the project's id. Types turned off or only proposed are left out unless
        ``include_deprecated`` / ``include_proposed`` ask for them. With a project,
        ``settings`` is its card type settings (``inherit``, ``agent_may_change``)
        and ``kept_types`` the built-in types it is offered whatever it chooses.
        ``include_counts`` adds ``card_count`` (live cards) to each type and
        ``unlisted_types``: types the project's cards carry that are not listed.
        """
        return self._run(
            ops.build_knowledge_list_wiki_page_types(
                project_id=project_id,
                include_proposed=include_proposed,
                include_deprecated=include_deprecated,
                include_counts=include_counts,
            )
        )

    @returns(ops.build_knowledge_update_wiki_page_type)
    def update_card_type(
        self,
        key: str,
        *,
        scope: str = "project",
        project_id: str | None = None,
        label: str | None = None,
        description: str | None = None,
        when_to_use: str | None = None,
        when_not_to_use: str | None = None,
        aliases: list[str] | None = None,
        fields: list[dict[str, Any]] | None = None,
        grain: str | None = None,
        status: str | None = None,
    ) -> Any:
        """Change a card type as one scope holds it. Returns ``{page_type}``.

        ``scope`` is ``project`` (one project, named by ``project_id``), ``org``
        (every project in the organization without its own copy) or ``global``;
        only scope ``project`` takes ``project_id``. A field left out is kept; a
        list given replaces that list. ``fields`` is the type's fields in order,
        each ``{"name", "required"}``; ``grain`` is ``card`` or ``row``;
        ``status`` ``deprecated`` turns the type off for the scope.

        Changing a type the scope only inherits gives the scope its own copy, and
        ``page_type["overrides"]`` names the scope that copy stands in front of
        (:meth:`remove_card_type_copy` drops it). A project's owners change its
        types and an organization's admins the organization's. Cards already
        written are not rewritten; later compiles use the new definition.
        """
        body = clean_body(
            {
                "scope": scope,
                "project_id": project_id,
                "label": label,
                "description": description,
                "when_to_use": when_to_use,
                "when_not_to_use": when_not_to_use,
                "aliases": aliases,
                "fields": fields,
                "grain": grain,
                "status": status,
            }
        )
        return self._run(ops.build_knowledge_update_wiki_page_type(key, body=body))

    @returns(ops.build_knowledge_remove_wiki_page_type_override)
    def remove_card_type_copy(
        self, key: str, *, scope: str = "project", project_id: str | None = None
    ) -> Any:
        """Remove a scope's own copy of a card type. Returns ``{removed, key, scope}``.

        ``scope`` is ``project`` (with ``project_id``) or ``org``; the scope goes
        back to the type it inherits. A type defined only in that scope raises
        ``ConflictError``: turn it off with ``update_card_type(key,
        status="deprecated")`` instead.
        """
        return self._run(
            ops.build_knowledge_remove_wiki_page_type_override(
                key, scope=scope, project_id=project_id
            )
        )

    @returns(ops.build_knowledge_convert_row_type_to_field)
    def make_row_type_a_field(
        self,
        key: str,
        *,
        on_type: str,
        name: str,
        description: str | None = None,
        scope: str = "project",
        project_id: str | None = None,
    ) -> Any:
        """Make the row type ``key`` a field ``name`` of the card type ``on_type``.

        ``description`` says what to record under the field. The row type is
        turned off for the scope; cards and rows already written are unchanged.
        ``scope`` is ``project`` (with ``project_id``) or ``org``.
        """
        return self._run(
            ops.build_knowledge_convert_row_type_to_field(
                key,
                body=clean_body(
                    {
                        "on_type": on_type,
                        "name": name,
                        "description": description,
                        "scope": scope,
                        "project_id": project_id,
                    }
                ),
            )
        )

    @returns(ops.build_knowledge_copy_wiki_page_types)
    def copy_card_types(
        self,
        from_project_id: str,
        to_project_id: str,
        keys: list[str],
        *,
        replace: bool = False,
    ) -> Any:
        """Copy card types one project defines into another project.

        Returns ``{copied, replaced, skipped}``. The destination gets its own
        copies; the caller must see the source project and own the destination.
        A key the source does not itself define is skipped as ``not_found``, and
        one the destination already defines as ``exists`` unless ``replace`` is
        true. Cards are not copied or changed.
        """
        body = {
            "from_project_id": from_project_id,
            "to_project_id": to_project_id,
            "keys": list(keys),
            "replace": replace,
        }
        return self._run(ops.build_knowledge_copy_wiki_page_types(body=body))

    @returns(ops.build_knowledge_promote_wiki_page_types)
    def promote_card_types(self, project_id: str, keys: list[str], *, replace: bool = False) -> Any:
        """Copy card types a project defines up to the organization.

        Returns ``{copied, replaced, skipped}``. ``project_id`` is the project
        whose types to promote (the route names it ``source``); every project
        without a copy of its own then inherits them. Needs an organization
        admin. A key the project does not itself define is skipped as
        ``not_found``, and one the organization already defines as ``exists``
        unless ``replace`` is true.
        """
        body = {"source": project_id, "keys": list(keys), "replace": replace}
        return self._run(ops.build_knowledge_promote_wiki_page_types(body=body))

    # -- the cards a project already has ----------------------------------------

    @returns(ops.build_knowledge_rename_wiki_page_type_fields)
    def rename_card_type_fields(
        self,
        key: str,
        project_id: str,
        renames: Mapping[str, str] | list[dict[str, str]],
        *,
        dry_run: bool = True,
        continue_event_id: str | None = None,
    ) -> Any:
        """Rename fields on every card of a type in one project. A dry run unless asked.

        ``renames`` is ``{old: new}`` or the route's own list of ``{"old", "new"}``;
        the type's field list is renamed with the cards, and rows that become the
        same field are merged with their citations kept. With ``dry_run`` (the
        default) nothing is written and the answer holds the counts: ``cards``,
        ``cards_changed``, ``rows_renamed``, ``rows_merged`` and the renames it
        would have ``applied`` and ``refused``. ``dry_run=False`` changes at most
        500 cards a call; ``cards_remaining`` says how many are left, and the same
        call again changes the next ones. Pass the first call's ``undo_event_id``
        as ``continue_event_id`` on the later calls and the whole rename stays one
        entry in the project's activity, with one undo. An applied change answers ``commit_id``
        and ``undo_event_id`` and shows in the project's activity, where an owner
        can undo it. Only the project's owners may; ``ConflictError`` while
        Knowledge runs maintenance on the project.
        """
        pairs = (
            [{"old": old, "new": new} for old, new in renames.items()]
            if isinstance(renames, Mapping)
            else [dict(pair) for pair in renames]
        )
        body = clean_body(
            {
                "project_id": project_id,
                "renames": pairs,
                "dry_run": dry_run,
                "continue_event_id": continue_event_id,
            }
        )
        return self._run(ops.build_knowledge_rename_wiki_page_type_fields(key, body=body))

    @returns(ops.build_knowledge_retype_wiki_page_type_cards)
    def retype_cards(
        self,
        key: str,
        project_id: str,
        to_type: str,
        *,
        dry_run: bool = True,
        suggestion_id: str | None = None,
        continue_event_id: str | None = None,
    ) -> Any:
        """Move every card of a type in one project to another type. A dry run unless asked.

        ``to_type`` must be an active type in the project whose instances are
        cards. With ``dry_run`` (the default) nothing is written and the answer
        holds ``cards``, the number that would move. ``dry_run=False`` moves at
        most 500 cards a call (``cards_retyped``, ``cards_skipped``,
        ``cards_remaining``) and answers ``commit_id`` and ``undo_event_id``. Pass
        that ``undo_event_id`` as ``continue_event_id`` on the later calls of a
        large move and it stays one entry in the project's activity, with one undo. A
        card's text is not changed: if the two types name a field differently,
        rename the fields on the target type afterwards
        (:meth:`rename_card_type_fields`). Only the project's owners may.

        ``suggestion_id`` is the pending suggestion this move answers (from
        :meth:`card_type_suggestions`); an apply that leaves no card of the type
        marks it applied. ``key`` may be a type no longer defined when the
        project still has cards carrying it.
        """
        body = clean_body(
            {
                "project_id": project_id,
                "to_type": to_type,
                "dry_run": dry_run,
                "suggestion_id": suggestion_id,
                "continue_event_id": continue_event_id,
            }
        )
        return self._run(ops.build_knowledge_retype_wiki_page_type_cards(key, body=body))

    # -- card type settings, history and suggestions ------------------------------

    def card_type_settings(self, project_id: str) -> Any:
        """A project's card type settings: ``{inherit, agent_may_change}``.

        ``inherit`` is ``organization`` (the organization's types and the built-in
        ones), ``builtin`` (the built-in ones only) or ``own`` (only the types the
        project defines). ``agent_may_change`` says whether Beakr itself may add or
        change the project's types. Read from :meth:`list_card_types`, which
        answers them beside the types.
        """
        return self._run_then(
            ops.build_knowledge_list_wiki_page_types(project_id=project_id),
            lambda listing: (listing or {}).get("settings"),
        )

    @returns(ops.build_knowledge_change_card_type_settings)
    def set_card_type_settings(
        self,
        project_id: str,
        *,
        inherit: str | None = None,
        agent_may_change: bool | None = None,
        keep_types_in_use: bool = False,
        dry_run: bool = True,
    ) -> Any:
        """Change where a project's card types come from, or who may change them.

        A dry run unless ``dry_run=False``: it writes nothing and answers
        ``current``, ``requested`` and ``types_dropped``, each type the project
        would stop being offered with its ``live_cards``. Pass ``inherit``
        (``organization``, ``builtin`` or ``own``), ``agent_may_change``, or both.
        ``own`` needs the project to define at least one active type whose
        instances are cards. Applying never changes or removes a card or a type:
        a dropped type that still has cards gets a suggestion to move them
        (:meth:`card_type_suggestions`), or with ``keep_types_in_use`` becomes one
        of the project's own types (``types_kept``). An applied change answers
        ``history_id`` and ``undo_event_id``. Only the project's owners may.
        """
        body = clean_body(
            {
                "project_id": project_id,
                "inherit": inherit,
                "agent_may_change": agent_may_change,
                "keep_types_in_use": keep_types_in_use,
                "dry_run": dry_run,
            }
        )
        return self._run(ops.build_knowledge_change_card_type_settings(body=body))

    def card_type_history(
        self,
        *,
        project_id: str | None = None,
        scope: str | None = None,
        key: str | None = None,
        include_suggestions: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """One page of the changes to card types, newest first: ``{data, next_cursor, has_more}``.

        A project's (``project_id``) or the organization's (``scope="org"``),
        narrowed to one type with ``key``. Each entry says who (``actor_type``,
        ``actor_user_id`` or ``actor_name``), when, the ``kind`` (``edited``,
        ``turned_off``, ``created``, ``settings_changed``, ``undone`` ...), a
        ``title``, the fields that ``changed`` and the definition ``before`` and
        ``after``. ``include_suggestions`` adds suggestions with their ``state``.
        :meth:`iter_card_type_history` follows every page.
        """
        return self._run(
            ops.build_knowledge_list_card_type_history(
                project_id=project_id,
                scope=scope,
                key=key,
                include_suggestions=include_suggestions,
                limit=limit,
                cursor=cursor,
            )
        )

    def iter_card_type_history(
        self,
        *,
        project_id: str | None = None,
        scope: str | None = None,
        key: str | None = None,
        include_suggestions: bool | None = None,
        page_size: int | None = None,
    ) -> Any:
        """Every entry :meth:`card_type_history` lists, following its cursor."""
        return self._iter(
            ops.build_knowledge_list_card_type_history(
                project_id=project_id,
                scope=scope,
                key=key,
                include_suggestions=include_suggestions,
                limit=page_size,
            )
        )

    def card_type_suggestions(
        self, project_id: str, *, limit: int | None = None, cursor: str | None = None
    ) -> Any:
        """One page of what waits for a person in a project: ``{data, next_cursor, has_more}``.

        ``kind`` is ``held_back`` (a type Beakr was not allowed to add) or
        ``retype_suggested`` (cards to move, with ``details["from_type"]`` and
        ``details["cards"]``). Answer a move with :meth:`retype_cards` and its
        ``suggestion_id``, or :meth:`dismiss_card_type_suggestion`.
        """
        return self._run(
            ops.build_knowledge_list_card_type_suggestions(
                project_id=project_id, limit=limit, cursor=cursor
            )
        )

    def iter_card_type_suggestions(self, project_id: str, *, page_size: int | None = None) -> Any:
        """Every suggestion :meth:`card_type_suggestions` lists, following its cursor."""
        return self._iter(
            ops.build_knowledge_list_card_type_suggestions(project_id=project_id, limit=page_size)
        )

    @returns(ops.build_knowledge_dismiss_card_type_suggestion)
    def dismiss_card_type_suggestion(self, suggestion_id: str, project_id: str) -> Any:
        """Dismiss one suggestion. Returns ``{suggestion, activity_event_id}``.

        ``project_id`` is the project the suggestion belongs to. Only the
        project's owners may; ``ConflictError`` if it was already answered.
        """
        return self._run(
            ops.build_knowledge_dismiss_card_type_suggestion(
                suggestion_id, body={"project_id": project_id}
            )
        )

    # -- the compile queue -----------------------------------------------------------

    @returns(ops.build_knowledge_get_compile_queue)
    def compile_queue(
        self,
        project_id: str,
        *,
        attachment_id: str | None = None,
        artifact_id: str | None = None,
    ) -> Any:
        """How many Knowledge compiles are queued and running, and the last cancel's progress.

        For the whole project, or one connected source (``attachment_id``) or one
        uploaded file (``artifact_id``); give at most one. Answers ``{queued,
        running, last_cancel}``; ``last_cancel`` is ``None`` or ``{cancel_id,
        status, done, total, summary, cancelled_by, cancelled_at}``, where
        ``status`` is ``"cancelling"`` until ``done`` reaches ``total``.
        """
        return self._run(
            ops.build_knowledge_get_compile_queue(
                project_id, attachment_id=attachment_id, artifact_id=artifact_id
            )
        )

    @returns(ops.build_knowledge_cancel_queued_compiles)
    def cancel_queued_compiles(
        self,
        project_id: str,
        *,
        attachment_id: str | None = None,
        artifact_id: str | None = None,
    ) -> Any:
        """Cancel the queued Knowledge compiles of a project, or of one source or file.

        Compiles already running finish (``already_running`` counts them) and pages
        Knowledge already wrote stay. Cancelling a source's compiles
        (``attachment_id``) also stops that source's running sync; cancelling the
        whole project's (neither id) stops every running sync in the project
        (``sync_stopped``, ``syncs_stopped``; ``sync_job_id`` for a source). Answers
        ``{cancel_id, scope, status, will_be_skipped, already_running, sync_stopped,
        syncs_stopped, sync_job_id, next_sync, note}`` while the queue drains;
        :meth:`compile_queue` follows it. A project admin may cancel anything in the
        project, the person who attached a source or uploaded a file its compiles.
        """
        body: dict[str, str] = {}
        if attachment_id is not None:
            body["attachment_id"] = attachment_id
        if artifact_id is not None:
            body["artifact_id"] = artifact_id
        return self._run(ops.build_knowledge_cancel_queued_compiles(project_id, body=body))
