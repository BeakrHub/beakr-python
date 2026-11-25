"""Integrations: connect, list, status, disconnect, and query what is connected.

Connecting finishes in a browser, where the provider's OAuth consent happens:
``connect`` returns ``connect_url`` to open. Everything else works from a key
or token (``integrations:read`` / ``integrations:admin``).

The caller's connected integrations can be queried the way Beakr's own agent
does: :meth:`Integrations.tools` lists the read-only tools for them and
:meth:`Integrations.call_tool` runs one (:meth:`Integrations.call` is the same
method under the name the MCP ``integrations`` tool and the CLI give it).

Example::

    link = beakr.integrations.connect("slack")
    print(link["connect_url"])
    for item in beakr.integrations.list()["data"]:
        print(item["provider"], [p["project_id"] for p in item["projects"]])
    for tool in beakr.integrations.tools(provider="slack"):
        print(tool["name"], tool["description"])
    found = beakr.integrations.call_tool("slack_search_messages", {"query": "assay handover"})
    print(found["content"])
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import iterates, returns, returns_all
from beakr_sdk.resources._base import Resource


class Integrations(Resource[M]):
    @returns(ops.build_integrations_list_integrations)
    def list(self) -> Any:
        """``{data: [...]}``: connections you can see, each with the projects it feeds."""
        return self._run(ops.build_integrations_list_integrations())

    @returns(ops.build_integrations_list_project_integrations)
    def list_for_project(
        self, project_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> Any:
        """``{data: [...]}``: the connections a project's sources can be attached from.

        Your own and the organization's active connections: the ones
        ``POST /v1/projects/{project_id}/sources`` takes as ``connector_id``. Each
        item has the shape :meth:`list` gives, with ``projects`` naming only this
        project's sources. A key limited to projects may call it for one of them,
        where the organization-wide :meth:`list` is refused (403).
        """
        return self._run(
            ops.build_integrations_list_project_integrations(project_id, cursor=cursor, limit=limit)
        )

    @returns(ops.build_integrations_connect_integration)
    def connect(self, provider: str, *, scope: Literal["user", "org"] = "user") -> Any:
        """``{provider, scope, connect_url, expires_at}``: open ``connect_url`` in a browser."""
        return self._run(
            ops.build_integrations_connect_integration(provider, body={"scope": scope})
        )

    @returns(ops.build_integrations_integration_status)
    def status(self, connector_id: str) -> Any:
        """Health plus each project source's latest sync."""
        return self._run(ops.build_integrations_integration_status(connector_id))

    @returns(ops.build_integrations_disconnect_integration)
    def disconnect(self, connector_id: str) -> Any:
        """Disconnect, including the connection at the provider."""
        return self._run(ops.build_integrations_disconnect_integration(connector_id))

    @returns(ops.build_integrations_cancel_source_sync)
    def cancel_source_sync(self, project_id: str, attachment_id: str) -> Any:
        """Stop a project source's running sync, Notion follow-up syncs included.

        Items already being read finish; the rest are skipped, and the next sync
        picks them up. Compiles the sync already queued still run:
        ``knowledge.cancel_queued_compiles(project_id, attachment_id=...)`` cancels
        them (and stops the sync too). Answers ``{sync_job_id, status:
        "cancelling", cancelled_by, next_sync, note}``; ``jobs.get(sync_job_id)``
        follows it to ``cancelled``. ``ConflictError`` (``no_running_sync``) when no
        sync is running. The person who attached the source, a project admin or an
        organization admin may.
        """
        return self._run(ops.build_integrations_cancel_source_sync(project_id, attachment_id))

    @returns(ops.build_integrations_list_tracked_items)
    def tracked_items(
        self,
        project_id: str,
        attachment_id: str,
        *,
        state: Literal["pending", "ingested", "partial", "skipped", "failed", "removed"]
        | None = None,
        reason_code: str | None = None,
        search: str | None = None,
        since: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """One page of a project source's tracked items, and what became of each.

        Answers ``{data, next_cursor, has_more, provider, kind, counts, matched}``.
        Each item's ``state`` is ``pending`` (not read into Knowledge yet),
        ``ingested``, ``partial`` (in Knowledge with ``unfinished_changes``
        planned changes that did not land: ``compile_partial`` retried by the next
        sync, ``compile_not_converging`` could not be applied), ``skipped``,
        ``failed`` (nothing of its version reached Knowledge) or ``removed``; a
        skipped, failed or partial item gives ``reason_code`` and ``reason``,
        including when Knowledge read it and found nothing to add
        (``nothing_to_add``) and why a compile failed (``compile_failed``,
        ``import_gave_up``).
        ``counts`` cover the whole source; ``state``, ``reason_code``, ``search``
        (title text) and ``since`` (synced at or after an ISO 8601 time) narrow
        the page, and ``matched`` says how many they keep. A sync's
        ``jobs.get(job_id)["result"]["outcomes"]`` links here
        (``tracked_items_path``) narrowed to that sync.
        """
        return self._run(
            ops.build_integrations_list_tracked_items(
                project_id,
                attachment_id,
                state=state,
                reason_code=reason_code,
                search=search,
                since=since,
                cursor=cursor,
                limit=limit,
            )
        )

    @returns(ops.build_integrations_recompile_project_source)
    def recompile(
        self,
        project_id: str,
        attachment_id: str,
        *,
        external_ids: Sequence[str] | None = None,
        all_items: bool = False,
        mode: Literal["retry", "full"] = "retry",
        dry_run: bool = False,
    ) -> Any:
        """Send a source's tracked items to Knowledge again, in any state.

        Name the items by their tracked-items ``external_id`` (``external_ids``),
        or pass ``all_items=True`` for every item the source tracks (up to 500 per
        request; ``over_cap`` counts the rest). ``mode="retry"`` sends again only
        items not in Knowledge at their current version (pending, failed,
        skipped); ``mode="full"`` also sends items already in Knowledge and has
        each whole document read again, for example after the source's guidance
        changed. A sync starts at once through the attaching person's connection
        (``sync`` is ``started`` with its ``sync_job_id``). ``dry_run=True`` counts
        and sends nothing. Pending items still on their way into Knowledge (their
        compile queued or running, or read in the last 30 minutes) are left out and
        counted in ``in_progress_left``.

        Anyone who can edit the project may. One request per source every ten
        minutes: a sooner one raises ``RateLimitError`` (``recompile_cooldown``).
        ``ConflictError`` (``connection_unusable``, ``knowledge_off``,
        ``sync_running``) when the source cannot be read, Knowledge is off for it,
        or a sync of it is running (what it reads is already on its way).
        """
        if (external_ids is None) == (not all_items):
            raise ValueError("pass external_ids or all_items=True, not both or neither")
        body: dict[str, Any] = {"mode": mode, "dry_run": dry_run}
        if all_items:
            body["all"] = True
        else:
            body["external_ids"] = list(external_ids or [])
        return self._run(
            ops.build_integrations_recompile_project_source(project_id, attachment_id, body=body)
        )

    @returns(ops.build_integrations_get_history_import_status)
    def history_import_status(self, project_id: str, attachment_id: str) -> Any:
        """Whether a source takes a history import, whether one runs, and how it ended.

        Answers ``{provider, supported, status, request, progress, outcome,
        last_import, last_import_cancelled, last_outcome}``. ``status`` is
        ``idle``, ``pending``, ``running`` or ``cancelling``. ``outcome`` (the
        running import) and ``last_outcome`` (the last one) count its items
        ``in_knowledge``, ``skipped``, ``failed`` and ``left``, with
        ``skipped_reasons``, ``failed_reasons`` and ``left_reasons``, ``removed``
        for items deleted in the source, ``state`` (``working`` or ``finished``),
        ``finished_at``, ``followup`` and a ``summary`` sentence. Scope
        ``projects:read``; a key limited to projects may call it for one of them.
        """
        return self._run(
            ops.build_integrations_get_history_import_status(project_id, attachment_id)
        )

    @iterates(ops.build_integrations_list_tracked_items)
    def iter_tracked_items(
        self,
        project_id: str,
        attachment_id: str,
        *,
        state: Literal["pending", "ingested", "partial", "skipped", "failed", "removed"]
        | None = None,
        reason_code: str | None = None,
        search: str | None = None,
        since: str | None = None,
        page_size: int | None = None,
    ) -> Any:
        """:meth:`tracked_items`, every item, following the cursor."""
        return self._iter(
            ops.build_integrations_list_tracked_items(
                project_id,
                attachment_id,
                state=state,
                reason_code=reason_code,
                search=search,
                since=since,
                limit=page_size,
            )
        )

    @returns_all(ops.build_integrations_list_integration_tools)
    def tools(self, *, provider: str | None = None, connector_id: str | None = None) -> Any:
        """Every read-only tool for the integrations the caller has connected and may use.

        Each tool has ``name``, ``provider``, ``description``, ``input_schema`` and
        the ``connections`` it can run against. Narrow with ``provider`` or one
        ``connector_id``.
        """
        return self._list_all(
            ops.build_integrations_list_integration_tools(
                provider=provider, connector_id=connector_id
            )
        )

    @iterates(ops.build_integrations_list_integration_tools)
    def iter_tools(self, *, provider: str | None = None, connector_id: str | None = None) -> Any:
        """:meth:`tools`, one page at a time, as an iterator."""
        return self._iter(
            ops.build_integrations_list_integration_tools(
                provider=provider, connector_id=connector_id
            )
        )

    @returns(ops.build_integrations_call_integration_tool)
    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        connector_id: str | None = None,
    ) -> Any:
        """Run one read-only tool (a name from :meth:`tools`) with its ``arguments``.

        Answers ``{tool, provider, connector_ids, content, is_error, sources}``.
        When the caller has the provider connected more than once, ``connector_id``
        picks the connection.
        """
        body: dict[str, Any] = {"arguments": dict(arguments or {})}
        if connector_id is not None:
            body["connector_id"] = connector_id
        return self._run(ops.build_integrations_call_integration_tool(name, body=body))

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        connector_id: str | None = None,
    ) -> Any:
        """:meth:`call_tool` by the name ``integrations command='call'`` and ``beakr
        integrations call`` give it: ``beakr.integrations.call("granola_get_note",
        {"note_id": "not_..."})``. A source record's :func:`beakr_sdk.sources.read_call`
        is exactly these arguments.
        """
        return self.call_tool(name, arguments, connector_id=connector_id)
