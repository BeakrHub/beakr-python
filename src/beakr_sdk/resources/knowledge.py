"""Knowledge: the knowledge door (read commands, proposed writes) and proposals.

``command()`` and ``write()`` reach every command and action the door has,
with arguments named exactly as the in-product tools name them. The methods
below are typed shortcuts for the common ones.

Example::

    hits = beakr.knowledge.sections("billing owner", scope="Platform")
    card = beakr.knowledge.cat("Stripe", scope="Platform")
    rows = beakr.knowledge.rows(table="facts", entity="Stripe")
    beakr.knowledge.add_fact(
        "Stripe", attribute="contract renewal", value="2027-01-31",
        evidence="{{doc:abc123}}", rationale="From the signed order form",
        scope="Platform",
    )

A write stages a proposal (unless the user's auto-approve applies it); review it
with ``proposals.show`` and apply it with ``proposals.accept``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.errors import KnowledgeCommandError
from beakr_sdk.resources._base import Resource, as_list
from beakr_sdk.resources.knowledge_pages import KnowledgePages

#: Seconds a research call waits for its answer, whatever the client's timeout:
#: the engine gives the research route up to 150s (an agentic loop that reads
#: documents from connected services), so a 60s client would give up first.
RESEARCH_TIMEOUT = 170.0
#: Seconds a quick research call waits: the engine ends a quick run within 16s.
QUICK_RESEARCH_TIMEOUT = 30.0


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


class Proposals(Resource[M]):
    """Staged knowledge writes: ``/v1/knowledge/wiki/proposals``."""

    def list(
        self,
        *,
        status: str = "pending",
        project_id: str | None = None,
        limit: int = 50,
    ) -> Any:
        return self._run_then(
            ops.build_knowledge_list_proposals(status=status, project_id=project_id, limit=limit),
            # The paginated envelope (``data``), or the older ``proposals`` wrapper.
            lambda r: as_list(r, "data", "proposals"),
        )

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

    def create(self, action: str, *, rationale: str | None = None, **fields: Any) -> Any:
        """Stage a Knowledge change through the proposals route.

        ``action`` and fields are those of the Knowledge write door (``new``,
        ``edit``, ``archive`` ...); :meth:`Knowledge.write` is the usual way in.
        The route also takes the CLI's proposal shapes (``create``, ``edit``,
        ``find_replace``, ``mv``, ``archive`` with a ``summary``), for which a
        rationale is optional.
        """
        body = clean_body({"action": action, "rationale": rationale, **fields})
        return self._run(ops.build_knowledge_create_proposal(body=body))


class Knowledge(Resource[M]):
    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.proposals: Proposals[M] = Proposals(client)
        self.pages: KnowledgePages[M] = KnowledgePages(client)

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
        (Facts) reads each card's value on that date. ``group_by`` answers counts
        and totals per group instead of rows; ``regex`` reads ``attribute``, ``to``
        and ``query`` as regular expressions; ``view`` is ``values`` (default),
        ``fields`` or ``groups``; ``field_id`` selects one field as the field
        inventory names it; ``match`` is ``exact``, ``similar`` or ``meaning``
        (the default) for how ``attribute`` finds a field; ``after_card`` continues
        a card scan that stopped at its cap.
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

    def research(self, query: str, *, project_id: str | None = None, quick: bool = False) -> Any:
        """A researched, cited answer across the wiki, documents and connected services.

        Waits up to :data:`RESEARCH_TIMEOUT` seconds (or the client's timeout,
        if longer): the engine allows research up to 150s. With ``quick=True``
        the engine answers within 15 seconds from one search of Knowledge and
        uploaded documents (no connected services); the answer's ``engine`` is
        ``quick`` and ``stopped_early`` is true when it is the cited results
        without a written summary.
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
