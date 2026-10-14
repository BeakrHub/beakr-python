"""Conversations imported from outside agents (Claude Code, Codex, ChatGPT).

The engine keys a conversation on (org, user, client, session_id). A batch
sent WITH ``base_index`` starts at that position: send only the messages after
the ones already imported, with ``base_index`` = how many those are (the CLI's
hooks keep a per-session cursor for this). A batch sent WITHOUT ``base_index``
is read as the whole transcript from position 0: identical leading messages are
kept, and anything after the first difference is replaced, so never send just
a tail without ``base_index``. A ``base_index`` past what is stored raises
:class:`~beakr_sdk.errors.ConflictError` whose
``body["error"]["expected_base_index"]`` is the stored count to resend from
(:func:`expected_base_index` reads it). Imported conversations are private to
the caller.

Example::

    # compound=True imports and queues compounding in one request; the engine
    # holds the message text only until that capture reads it.
    imported = beakr.conversations.import_(
        client="claude-code",
        session_id="5f2c...",
        # ``author``: "human" only for a turn the host marks as typed by a person;
        # imported-session learning quotes no other turn. Omitted: "unknown".
        messages=[
            {"role": "user", "content": "...", "author": "human"},
            {"role": "assistant", "content": "...", "author": "agent"},
        ],
        interactive=True,
        compound=True,
    )
    imported["compound_queued"]  # False: nothing unread to compound
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import Call, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.errors import BeakrError, ConflictError
from beakr_sdk.resources._base import Resource

#: ``chatgpt-export`` (a conversation from a ChatGPT data export) is stored as
#: ``chatgpt``; the engine accepts both names.
CONVERSATION_CLIENTS = ("claude-code", "codex", "chatgpt", "chatgpt-export", "generic")

#: ``compound_status`` values. ``partial``, ``skipped`` and ``failed`` keep the
#: messages that were not processed (and their text), so compounding again
#: retries them; ``last_compound_result`` carries the outcome and its reason.
COMPOUND_STATUSES = ("pending", "queued", "running", "done", "partial", "skipped", "failed")


def expected_base_index(exc: BeakrError) -> int | None:
    """The stored message count a refused incremental import must resend from.

    ``None`` when ``exc`` is not that refusal (an idempotency conflict is also
    a 409, without this field).
    """
    if not isinstance(exc, ConflictError) or not isinstance(exc.body, dict):
        return None
    for container in (exc.body.get("error"), exc.body.get("detail")):
        if isinstance(container, dict) and isinstance(container.get("expected_base_index"), int):
            return int(container["expected_base_index"])
    return None


class Conversations(Resource[M]):
    def import_(
        self,
        *,
        client: str,
        session_id: str,
        messages: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        files_touched: list[str] | None = None,
        interactive: bool = False,
        store_full_text: bool | None = None,
        base_index: int | None = None,
        sources_read: list[dict[str, Any]] | None = None,
        tools_used: list[dict[str, Any]] | None = None,
        compound: bool = False,
        hold_text: bool = False,
    ) -> Any:
        """Import (or append to) a session. Returns ``{conversation_id, created,
        appended, revision, message_count, stored, ...}``.

        ``base_index`` places ``messages`` after that many stored messages; without
        it, ``messages`` is the whole transcript and the engine keeps the stored
        prefix whose hashes match and replaces the rest. Not retried after an
        ambiguous failure; callers resend from their own cursor, which the
        engine reconciles by message hash.

        ``sources_read`` reports what the session read, so freshness can later
        check it: ``{"page_id": ..., "revision": n}`` for a Knowledge page (send
        the revision you read; without it the read is listed but not checked),
        ``{"document_id": ..., "version_id": ...}`` for a document (the file's
        ``current_version_id`` when it was read), or ``{"source_ref": ...,
        "version": ...}`` for a source from a Knowledge read. Pages the
        session's compounding writes are never counted as reads.

        ``tools_used`` is ``[{"name", "count"}]``, the session's tool calls so far
        as counted from its transcript; a later import's larger count is kept.

        Message text is stored only by opt-in: ``store_full_text`` True keeps
        it, False does not, and ``None`` (the default) follows your per-client
        setting (:meth:`get_settings`).
        ``compound`` queues compounding of the unread messages and holds their
        text, for a bounded time, until that capture reads it. ``hold_text``
        holds the text the same way without queuing, for a session sent in
        several batches or compounded later with ``engine.compound``. With none
        of the three, only the summary and metadata are kept and a later
        compound answers ``nothing_to_compound``, so ``messages`` sent with none
        of them and no ``summary`` are refused (422 ``summary_required``); a later
        batch of a session already stored with a summary needs none.
        """
        body = clean_body(
            {
                "client": client,
                "session_id": session_id,
                "base_index": base_index,
                "project_id": project_id,
                "title": title,
                "summary": summary,
                "messages": messages,
                "files_touched": files_touched,
                "sources_read": sources_read,
                "tools_used": tools_used,
                "interactive": interactive,
                "store_full_text": store_full_text,
                # Sent only when set, so a plain import's body is unchanged.
                "compound": compound or None,
                "hold_text": hold_text or None,
            }
        )
        return self._run(ops.build_conversations_import_conversation(body=body))

    def _list_call(
        self,
        *,
        client: str | None,
        project_id: str | None,
        q: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> Call:
        return ops.build_conversations_list_conversations(
            client=client, project_id=project_id, q=q, limit=limit, cursor=cursor
        )

    def list(
        self,
        *,
        client: str | None = None,
        project_id: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """One page: ``{data, next_cursor, has_more}``."""
        return self._run(
            self._list_call(client=client, project_id=project_id, q=q, limit=limit, cursor=cursor)
        )

    def iter(
        self,
        *,
        client: str | None = None,
        project_id: str | None = None,
        q: str | None = None,
        page_size: int | None = None,
    ) -> Any:
        call = self._list_call(
            client=client, project_id=project_id, q=q, limit=page_size, cursor=None
        )
        return self._iter(call)

    @returns(ops.build_conversations_get_conversation)
    def get(self, conversation_id: str) -> Any:
        """One conversation, including ``compound_status``, ``compounded_through`` and
        ``last_compound_result`` (``{status, reason?, error?, ...}`` or ``None``)."""
        return self._run(ops.build_conversations_get_conversation(conversation_id))

    @returns(ops.build_conversations_delete_conversation)
    def delete(self, conversation_id: str) -> Any:
        return self._run(ops.build_conversations_delete_conversation(conversation_id))

    @returns(ops.build_conversations_search)
    def search(
        self,
        q: str,
        *,
        project_id: str | None = None,
        include_threads: bool = True,
        match: str = "all",
        limit: int | None = None,
    ) -> Any:
        """Your own conversations about ``q``, best first: ``{data: [hit], query}``.

        Searches each imported session's index (title, summaries, entities) and
        your Beakr chats by title. ``match="all"`` reads ``q`` as search syntax
        (every word must appear; quotes and ``-word`` work); ``match="any"`` takes a
        natural-language question and ranks by how many of its words match. A hit
        has ``kind`` (``imported`` or ``ask_thread``), ``summary``, ``entities``,
        ``findings``, ``outputs`` and ``href``.
        """
        return self._run(
            ops.build_conversations_search(
                q=q,
                project_id=project_id,
                include_threads=bool(include_threads),
                match=match,
                limit=limit,
            )
        )
