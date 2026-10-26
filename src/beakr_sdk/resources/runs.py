"""Assistant runs: ask Beakr's agent, then stream or poll the run.

Example::

    answer = beakr.runs.answer("What changed in Platform this week?", project_id=project_id)
    print(answer["text"])                       # citations rendered as [1], [2], ...
    for n, source in enumerate(answer["sources"], 1):
        print(n, source["name"], source.get("location", {}))

    # Or show the answer as it is written:
    run = beakr.runs.start("What changed in Platform this week?", project_id=project_id)
    for chunk in beakr.runs.stream_text(run["run_id"]):
        print(chunk, end="", flush=True)

``runs.stream`` yields the raw events for anything else (tool progress, widgets).
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from beakr_sdk._calls import adjusted, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import iterates, returns
from beakr_sdk.answers import AnswerBuilder, TextStream, answer_delta
from beakr_sdk.resources._base import Resource


class Threads(Resource[M]):
    """The caller's assistant threads."""

    @returns(ops.build_threads_list_my_threads)
    def list(
        self, *, q: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> Any:
        """One page of your threads, latest activity first: ``{data, next_cursor, has_more}``."""
        return self._run(ops.build_threads_list_my_threads(q=q, limit=limit, cursor=cursor))

    @iterates(ops.build_threads_list_my_threads)
    def iter(self, *, q: str | None = None, page_size: int | None = None) -> Any:
        """Every thread, following the cursor."""
        return self._iter(ops.build_threads_list_my_threads(q=q, limit=page_size))

    @returns(ops.build_threads_list_shared_threads)
    def shared(
        self,
        *,
        project_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        """One page of the threads shared to a project: ``{data, next_cursor, has_more}``."""
        return self._run(
            ops.build_threads_list_shared_threads(project_id=project_id, limit=limit, cursor=cursor)
        )

    @returns(ops.build_threads_get_thread_detail)
    def detail(
        self,
        thread_id: str,
        *,
        limit: int | None = None,
        before_message_id: str | None = None,
    ) -> Any:
        """A thread you can see (yours or shared), with paged messages."""
        return self._run(
            ops.build_threads_get_thread_detail(
                thread_id, limit=limit, before_message_id=before_message_id
            )
        )


class Runs(Resource[M]):
    def start(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        project_id: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        timezone: str | None = None,
        files: list[str] | None = None,
        knowledge: bool = True,
        idempotency_key: str | None = None,
    ) -> Any:
        """Start a run (a new thread unless ``thread_id``). Returns ``{run_id, thread_id, ...}``.

        ``knowledge`` lets the agent read the organization's Knowledge (its
        wiki) while it answers. The server treats a run that does not ask for it
        as one with Knowledge turned off, so it is on unless ``knowledge=False``.

        ``project_id`` scopes the run to that project and saves its new
        conversation there, which needs member access. A caller who can only
        read the project (a viewer, or an external user whose label grants it)
        still gets the run, with a private conversation.

        ``files`` are ids of files you can read (``files.upload(...)["artifact"]["id"]``),
        given to the agent the way files attached in Beakr's chat are; the
        request fails with ``not_found`` if any is not readable.

        The run route deduplicates on the body's ``idempotency_key``; one is
        generated when omitted, and kept across retries.
        """
        scope: dict[str, Any] = {"knowledge_graph": knowledge}
        if project_id:
            scope["projects"] = [{"id": project_id}]
        body = clean_body(
            {
                "idempotency_key": idempotency_key or uuid.uuid4().hex,
                "message": message,
                "thread_id": thread_id,
                "home_project_id": project_id,
                "scope": scope,
                "mode": mode,
                "model": model,
                "timezone": timezone,
                "files": list(files) if files else None,
            }
        )
        # Retrying is safe: the route returns the existing run for the same body key.
        return self._run(
            adjusted(ops.build_runs_start_chat_run(body=body), server_deduplicated=True)
        )

    @returns(ops.build_runs_get_run_status)
    def get(self, run_id: str) -> Any:
        return self._run(ops.build_runs_get_run_status(run_id))

    @returns(ops.build_runs_cancel_run)
    def cancel(self, run_id: str) -> Any:
        return self._run(ops.build_runs_cancel_run(run_id))

    def stream(self, run_id: str, *, last_event_id: str | None = None) -> Any:
        """Events ``{type, thread_id, data, event_id}`` until ``stream_complete``."""
        return self._stream(
            adjusted(
                ops.build_runs_stream_run_events(run_id, last_event_id=last_event_id),
                response="json",
            )
        )

    def answer(
        self,
        question: str,
        *,
        project_id: str | None = None,
        thread_id: str | None = None,
        mode: str | None = None,
        files: list[str] | None = None,
        knowledge: bool = True,
    ) -> Any:
        """Ask the agent and wait for its answer: ``{text, sources, run_id}``.

        ``text`` is the final answer with each citation rendered as ``[n]``;
        ``sources[n-1]`` is that citation's public source record
        (:mod:`beakr_sdk.sources`): name, filename, provider, where the
        original lives, and the excerpt the agent read. Raises
        :class:`~beakr_sdk.answers.RunFailedError` when the run ends ``error``,
        ``failed`` or ``cancelled``, and :class:`~beakr_sdk.answers.RunNeedsInputError`
        (with the run's ``question``) when the agent stopped to ask the person
        something instead of answering. An awaitable on ``AsyncBeakr``.
        """
        start = {
            "project_id": project_id,
            "thread_id": thread_id,
            "mode": mode,
            "files": files,
            "knowledge": knowledge,
        }
        if inspect.isasyncgenfunction(self._client._stream_sse):  # type: ignore[attr-defined]
            return self._answer_async(question, start)
        run = self.start(question, **start)
        builder = AnswerBuilder(str(run.get("run_id") or run.get("id")))
        for event in self.stream(builder.run_id):
            builder.feed(event)
        # The run as the server reports it once the stream ends: its rendered
        # answer, or the question it parked on.
        return builder.result(run=self._run(ops.build_runs_get_run_status(builder.run_id)))

    async def _answer_async(self, question: str, start: dict[str, Any]) -> dict[str, Any]:
        run = await self.start(question, **start)
        builder = AnswerBuilder(str(run.get("run_id") or run.get("id")))
        async for event in self.stream(builder.run_id):
            builder.feed(event)
        state = await self._run(ops.build_runs_get_run_status(builder.run_id))
        return builder.result(run=state)

    def stream_text(self, run_id: str) -> Any:
        """The answer's visible text as it streams, with citations numbered ``[n]``.

        Thinking and tool progress are skipped; a ``{{key}}`` token split across
        events is held until it is complete. An async iterator on ``AsyncBeakr``.
        For the cited sources, use :meth:`answer` or the ``sources`` events of
        :meth:`stream`.
        """
        if inspect.isasyncgenfunction(self._client._stream_sse):  # type: ignore[attr-defined]
            return self._stream_text_async(run_id)
        return self._stream_text_sync(run_id)

    def _stream_text_sync(self, run_id: str) -> Iterator[str]:
        numbering = TextStream()
        for event in self.stream(run_id):
            chunk = numbering.feed(answer_delta(event))
            if chunk:
                yield chunk
        rest = numbering.finish()
        if rest:
            yield rest

    async def _stream_text_async(self, run_id: str) -> AsyncIterator[str]:
        numbering = TextStream()
        async for event in self.stream(run_id):
            chunk = numbering.feed(answer_delta(event))
            if chunk:
                yield chunk
        rest = numbering.finish()
        if rest:
            yield rest
